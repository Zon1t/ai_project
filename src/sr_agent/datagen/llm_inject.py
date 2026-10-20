"""LLM 결함 주입 (PRD F2-1 ②).

함수마다 LLM이 업무 로직 결함을 제안하면, 코드로 검증해 뮤턴트 후보(inject_type="llm")로 만든다.
- original이 함수 소스에 정확히 한 번 나와야 하고, 바꾼 파일이 파싱되며 AST가 실제로 달라야 한다.
- AST 뮤턴트와 결과 파일이 같으면 중복으로 버린다.
테스트로 선별하는 것은 inject.py가 한다. LLM 응답은 data/mutants/llm_bugs_raw.jsonl에 함수 단위로 저장한다
(이어서 실행, 다시 호출하지 않음). 후보는 저장된 응답에서 매번 결정적으로 다시 만든다.

실행: python -m sr_agent.datagen.llm_inject [--limit N]  (PYTHONPATH=src)
"""

import argparse
import ast
import hashlib
import json
import random
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel

from sr_agent import config, llm
from sr_agent.datagen.mutate import Mutant, _functions, _mutant_id, _Source, list_mutants, package_files, read_source
from sr_agent.datagen.tickets import load_jsonl
from sr_agent.runlog import RunLog, new_run_id

INSTRUCTIONS = (Path(__file__).parent / "prompts" / "bug_injector.md").read_text(encoding="utf-8")


class InjectedBug(BaseModel):
    bug_type: str
    description: str
    original: str
    mutated: str


class InjectedBugs(BaseModel):
    bugs: list[InjectedBug]


@dataclass(frozen=True)
class FunctionSpan:
    """후보 함수와 파일 안 위치 (문자 오프셋)."""

    method: str
    file: str
    module_doc: str
    start: int
    end: int
    source: str


def function_spans(target_root: Path | None = None) -> list[FunctionSpan]:
    root = Path(target_root or config.TARGET_ROOT)
    spans = []
    for path in package_files(root):
        text = read_source(path)
        tree = ast.parse(text)
        src = _Source(text)
        doc = (ast.get_docstring(tree) or path.stem).splitlines()[0]
        for method, func in _functions(tree, path.stem):
            start, end = src.offset(func.lineno, func.col_offset), src.end(func)
            spans.append(FunctionSpan(method, path.relative_to(root).as_posix(), doc, start, end, text[start:end]))
    return spans


def build_input(span: FunctionSpan, n: int = config.LLM_BUGS_PER_FUNCTION) -> str:
    return f"업무 영역: {span.module_doc}\n만들 결함 수: {n}\n\n[함수 소스]\n{span.source}"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def to_mutants(span: FunctionSpan, text: str, bugs: list[dict], known: set[str]) -> tuple[list[Mutant], Counter]:
    """LLM 제안을 검증해 뮤턴트로. known은 이미 있는 결함 버전 파일의 해시 (중복 판정, 갱신됨)."""
    rejects: Counter = Counter()
    mutants = []
    original_dump = ast.dump(ast.parse(text))
    for bug in bugs:
        original, mutated = bug.get("original", ""), bug.get("mutated", "")
        if not original.strip() or span.source.count(original) != 1:
            rejects["original_not_unique"] += 1
            continue
        start = span.start + span.source.index(original)
        end = start + len(original)
        new_text = text[:start] + mutated + text[end:]
        try:
            changed = ast.dump(ast.parse(new_text)) != original_dump
        except SyntaxError:
            rejects["syntax_error"] += 1
            continue
        if not changed:
            rejects["no_change"] += 1
            continue
        if (digest := _digest(new_text)) in known:
            rejects["duplicate"] += 1
            continue
        known.add(digest)
        mutants.append(Mutant(
            mutant_id=_mutant_id(span.file, start, end, mutated), operator="LLM",
            description=f"[{bug.get('bug_type', '')}] {bug.get('description', '')}", method=span.method,
            file=span.file, file_sha256=_digest(text), lineno=text.count("\n", 0, start) + 1,
            start=start, end=end, original=original, replacement=mutated, inject_type="llm",
        ))
    return mutants, rejects


def load_candidates(target_root: Path | None = None, raw_path: Path | None = None) -> tuple[list[Mutant], Counter]:
    """저장된 LLM 응답에서 검증을 통과한 후보를 만든다 (결정적)."""
    root = Path(target_root or config.TARGET_ROOT)
    raw = {r["method"]: r for r in load_jsonl(Path(raw_path or config.LLM_BUGS_RAW_PATH)) if r["status"] == "ok"}
    texts = {p.relative_to(root).as_posix(): read_source(p) for p in package_files(root)}
    known = {_digest(m.apply(texts[m.file])) for m in list_mutants(root)}
    candidates, rejects = [], Counter()
    for span in function_spans(root):
        if span.method in raw:
            ms, rj = to_mutants(span, texts[span.file], raw[span.method]["bugs"], known)
            candidates += ms
            rejects += rj
    return candidates, rejects


def request_bugs(client: OpenAI, span: FunctionSpan, log: RunLog | None = None) -> dict:
    """함수 하나에 대한 LLM 호출. 반환은 llm_bugs_raw.jsonl 한 줄."""
    record = {"method": span.method, "model": config.MODEL_BULK}
    try:
        res = llm.parse_structured(
            client, model=config.MODEL_BULK, instructions=INSTRUCTIONS, input=build_input(span),
            text_format=InjectedBugs, effort=config.LLMINJECT_REASONING_EFFORT,
        )
    except llm.LLMRefusal as e:
        return {**record, "status": "refused", "error": str(e)}
    except Exception as e:  # noqa: BLE001 - API 오류도 결과로 남기고 다음 함수로 진행
        return {**record, "status": "error", "error": f"{type(e).__name__}: {e}"}
    bugs = [b.model_dump() for b in res.parsed.bugs]
    if log:
        log.write(step="llm_inject", tool="llm", model=config.MODEL_BULK, method=span.method,
                  output_summary=f"{len(bugs)} bugs", tokens=res.usage, latency_s=res.latency_s)
    return {**record, "status": "ok", "bugs": bugs, "usage": res.usage}


def generate(
    client: OpenAI | None = None,
    raw_path: Path | None = None,
    limit: int | None = None,
    workers: int = config.TICKETGEN_WORKERS,
    seed: int = config.SEED,
    runs_dir: Path | None = None,
) -> dict:
    """아직 응답이 없는 함수에 대해 결함 제안을 받아 저장한다."""
    raw_path = Path(raw_path or config.LLM_BUGS_RAW_PATH)
    done = {r["method"] for r in load_jsonl(raw_path) if r["status"] == "ok"}
    todo = [s for s in function_spans() if s.method not in done]
    random.Random(seed).shuffle(todo)
    if limit is not None:
        todo = todo[:limit]
    client = client or llm.get_client()
    log = RunLog(new_run_id("llminject"), runs_dir)
    lock = threading.Lock()
    results: list[dict] = []
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    def run_one(span: FunctionSpan) -> None:
        result = request_bugs(client, span, log)
        with lock, open(raw_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            results.append(result)

    with ThreadPoolExecutor(workers) as executor:
        list(executor.map(run_one, todo))
    usage: Counter = Counter()
    for r in results:
        usage.update({k: v for k, v in r.get("usage", {}).items() if isinstance(v, int)})
    return {"run_id": log.run_id, "requested": len(todo), "already_done": len(done),
            "status": dict(Counter(r["status"] for r in results)), "usage": dict(usage)}


def main() -> None:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="LLM 결함 주입 제안 수집 (PRD F2-1 ②)")
    parser.add_argument("--limit", type=int, default=None, help="이번 실행에서 처리할 함수 수 (기본: 남은 전체)")
    parser.add_argument("--workers", type=int, default=config.TICKETGEN_WORKERS)
    args = parser.parse_args()
    load_dotenv(config.PROJECT_ROOT / ".env")
    summary = generate(limit=args.limit, workers=args.workers)
    candidates, rejects = load_candidates()
    summary["candidates"] = len(candidates)
    summary["rejected"] = dict(rejects)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
