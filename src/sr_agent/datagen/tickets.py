"""결함 버전마다 현업식 SR 티켓 2건을 LLM으로 작성한다 (PRD F2-3).

- ticket_a: 운영 담당자 관점(증상만), ticket_b: QA 관점(재현 조건). 스택트레이스는 F2-4에서 붙인다.
- LLM 입력에는 테스트 쪽 실패 정보만 넣고 결함 코드(diff, 제품 코드 소스)는 넣지 않는다.
- 제목·본문에 정답 함수명이나 코드 식별자가 있으면 코드로 걸러 1회 재시도한다.
- 응답은 data/tickets/drafts.jsonl에 결함 단위로 바로 저장한다 (중단 후 이어서 실행).

실행: python -m sr_agent.datagen.tickets [--limit N]  (PYTHONPATH=src)
"""

import argparse
import ast
import json
import random
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel

from sr_agent import config, llm
from sr_agent.datagen.traces import failure_excerpt
from sr_agent.runlog import RunLog, new_run_id

INSTRUCTIONS = (Path(__file__).parent / "prompts" / "ticket_writer.md").read_text(encoding="utf-8")
_SNAKE_CASE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b")


class TicketDraft(BaseModel):
    title: str
    body: str


class TicketPair(BaseModel):
    ticket_a: TicketDraft
    ticket_b: TicketDraft


@cache
def module_summary(module: str) -> str:
    """대상 모듈 docstring 첫 줄 (업무 영역)."""
    path = config.TARGET_ROOT / config.TARGET_SRC_SUBDIR / config.TARGET_PACKAGE / f"{module}.py"
    doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or module
    return doc.splitlines()[0]


def build_input(mutant: dict) -> str:
    """결함마다 바뀌는 입력. 지시문(INSTRUCTIONS)과 분리해 prompt caching이 앞부분에 걸리게 한다."""
    lines = [
        f"업무 영역: {module_summary(mutant['method'].split('.')[0])}",
        f"실패한 자동 테스트 수: {mutant['n_failed']}",
    ]
    for i, t in enumerate(mutant["traces"][: config.TICKET_FAILURES_IN_PROMPT], 1):
        lines += ["", f"[실패 정보 {i}]", failure_excerpt(t["trace"])]
    return "\n".join(lines)


def find_leaks(text: str, method: str) -> list[str]:
    """제목·본문에 들어가면 안 되는 것: 정답 함수명(단독·모듈 포함)과 snake_case 코드 식별자."""
    func = method.split(".")[-1]
    leaks = {name for name in (method, func) if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE)}
    leaks.update(_SNAKE_CASE.findall(text))
    return sorted(leaks)


def _pair_leaks(pair: TicketPair, method: str) -> list[str]:
    return sorted({
        leak for d in (pair.ticket_a, pair.ticket_b) for leak in find_leaks(f"{d.title}\n{d.body}", method)
    })


def _add_usage(total: Counter, usage: dict) -> None:
    total.update({k: v for k, v in usage.items() if isinstance(v, int)})


def write_tickets(client: OpenAI, mutant: dict, log: RunLog | None = None) -> dict:
    """결함 1개에 대한 티켓 2건. 반환은 drafts.jsonl 한 줄 (status: ok|rejected|refused|error)."""
    base_input = build_input(mutant)
    prompt_input = base_input
    usage: Counter = Counter()
    record = {"mutant_id": mutant["mutant_id"], "method": mutant["method"], "model": config.MODEL_BULK}
    for attempt in range(1, config.TICKETGEN_MAX_ATTEMPTS + 1):
        try:
            res = llm.parse_structured(
                client, model=config.MODEL_BULK, instructions=INSTRUCTIONS, input=prompt_input,
                text_format=TicketPair, effort=config.TICKETGEN_REASONING_EFFORT,
            )
        except llm.LLMRefusal as e:
            return {**record, "status": "refused", "attempts": attempt, "error": str(e), "usage": dict(usage)}
        except Exception as e:  # noqa: BLE001 - API 오류도 결과로 남기고 다음 결함으로 진행
            return {**record, "status": "error", "attempts": attempt, "error": f"{type(e).__name__}: {e}",
                    "usage": dict(usage)}
        _add_usage(usage, res.usage)
        pair: TicketPair = res.parsed
        leaks = _pair_leaks(pair, mutant["method"])
        if log:
            log.write(step="ticketgen", tool="llm", model=config.MODEL_BULK, mutant_id=mutant["mutant_id"],
                      attempt=attempt, output_summary=pair.ticket_a.title, leaks=leaks, tokens=res.usage,
                      latency_s=res.latency_s)
        if not leaks:
            return {**record, "status": "ok", "attempts": attempt, **pair.model_dump(), "usage": dict(usage)}
        # 재시도 안내는 입력 끝에 붙여 앞부분(지시문·결함 정보)의 캐시를 살린다
        prompt_input = (f"{base_input}\n\n[이전 응답의 문제]\n다음 코드 식별자가 들어 있었다: {', '.join(leaks)}\n"
                        "업무 용어로 바꿔 두 티켓을 다시 써라.")
    return {**record, "status": "rejected", "attempts": config.TICKETGEN_MAX_ATTEMPTS, "leaks": leaks,
            "usage": dict(usage)}


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def generate(
    client: OpenAI | None = None,
    mutants_path: Path | None = None,
    drafts_path: Path | None = None,
    limit: int | None = None,
    workers: int = config.TICKETGEN_WORKERS,
    seed: int = config.SEED,
    runs_dir: Path | None = None,
) -> dict:
    """아직 ok 티켓이 없는 결함에 대해 티켓을 만든다. limit이면 시드로 섞은 순서의 앞 N개만."""
    mutants_path = Path(mutants_path or config.MUTANTS_PATH)
    drafts_path = Path(drafts_path or config.TICKET_DRAFTS_PATH)
    mutants = load_jsonl(mutants_path)
    done = {d["mutant_id"] for d in load_jsonl(drafts_path) if d["status"] == "ok"}
    todo = [m for m in mutants if m["mutant_id"] not in done]
    random.Random(seed).shuffle(todo)
    if limit is not None:
        todo = todo[:limit]

    client = client or llm.get_client()
    log = RunLog(new_run_id("ticketgen"), runs_dir)
    lock = threading.Lock()
    results: list[dict] = []
    drafts_path.parent.mkdir(parents=True, exist_ok=True)

    def run_one(mutant: dict) -> None:
        result = write_tickets(client, mutant, log)
        with lock, open(drafts_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            results.append(result)

    with ThreadPoolExecutor(workers) as executor:
        list(executor.map(run_one, todo))

    usage: Counter = Counter()
    for r in results:
        _add_usage(usage, r.get("usage", {}))
    return {
        "run_id": log.run_id,
        "requested": len(todo),
        "already_done": len(done),
        "status": dict(Counter(r["status"] for r in results)),
        "retried": sum(r.get("attempts", 1) > 1 for r in results),
        "usage": dict(usage),
        "drafts_path": str(drafts_path),
    }


def main() -> None:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="결함 버전마다 SR 티켓 2건 생성 (PRD F2-3)")
    parser.add_argument("--limit", type=int, default=None, help="이번 실행에서 처리할 결함 수 (기본: 남은 전체)")
    parser.add_argument("--workers", type=int, default=config.TICKETGEN_WORKERS)
    args = parser.parse_args()
    load_dotenv(config.PROJECT_ROOT / ".env")
    print(json.dumps(generate(limit=args.limit, workers=args.workers), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
