"""결함 버전 선별과 실패 정보 수집 (PRD F2-1, F2-2).

뮤턴트마다 임시 작업본(work/)에 결함을 심고 대상 프로젝트 테스트를 돌린다 (run_tests).
기존 테스트가 1개 이상 실패한 버전만 채택하고, 실패 테스트·트레이스를 함께 저장한다.
후보는 AST 뮤턴트와 LLM 주입 후보(llm_inject.py)이며, 주입 방식별 함수당 상한(config.MUTANT_CAPS)을 둔다.
target/ 원본은 읽기만 한다. 후보 순서는 config.SEED로 섞고 함수별로 돌아가며 고른다.

실행: python -m sr_agent.datagen.inject [--limit N]  (PYTHONPATH=src)
"""

import argparse
import hashlib
import json
import random
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue

from sr_agent import config
from sr_agent.datagen.mutate import Mutant, list_mutants, read_source
from sr_agent.tools.execution import run_tests
from sr_agent.tools.workspace import Workspace, compare_hashes, hash_tree, prepare_workspace

MUTANTS_FILENAME = "mutants.jsonl"
SUMMARY_FILENAME = "summary.json"


def order_for_sampling(mutants: list[Mutant], seed: int = config.SEED) -> list[Mutant]:
    """함수별로 섞은 뒤 함수를 돌아가며 하나씩 꺼내는 순서. 앞쪽 N개가 여러 함수에 고루 퍼진다."""
    rng = random.Random(seed)
    by_method: dict[str, list[Mutant]] = defaultdict(list)
    for m in mutants:
        by_method[m.method].append(m)
    methods = sorted(by_method)
    rng.shuffle(methods)
    for method in methods:
        rng.shuffle(by_method[method])
    ordered = []
    for round_ in range(max((len(v) for v in by_method.values()), default=0)):
        ordered.extend(by_method[m][round_] for m in methods if round_ < len(by_method[m]))
    return ordered


def classify(result: dict) -> str:
    """run_tests 결과 분류: killed(채택) / survived / timeout / collect_error / error."""
    if "error" in result:
        return "timeout" if result["error"].startswith("timeout") else "error"
    failed = result["failed"]
    if not failed:
        return "survived"
    if all("::" not in f["test"] for f in failed):
        return "collect_error"  # 테스트 실행 전 수집 단계에서만 실패
    return "killed"


def evaluate_mutant(ws: Workspace, mutant: Mutant, original_text: str, timeout: float | None = None) -> dict:
    """작업본에 결함을 심고 전체 테스트를 돌린 뒤 원래대로 되돌린다."""
    path = ws.work / mutant.file
    try:
        path.write_bytes(mutant.apply(original_text).encode("utf-8"))
        return run_tests(ws, "patched", timeout=timeout)
    finally:
        path.write_bytes(original_text.encode("utf-8"))


def to_record(mutant: Mutant, result: dict, original_text: str) -> dict:
    """채택한 결함 버전의 저장 형식 (data/mutants/mutants.jsonl 한 줄)."""
    failed = result["failed"]
    return {
        "mutant_id": mutant.mutant_id,
        "inject_type": mutant.inject_type,
        "operator": mutant.operator,
        "description": mutant.description,
        "method": mutant.method,
        "file": mutant.file,
        "lineno": mutant.lineno,
        "failed_tests": [f["test"] for f in failed],
        "n_failed": len(failed) + result.get("omitted", 0),
        "traces": failed[: config.MUTANT_TRACES_KEPT],
        "diff": mutant.diff(original_text),
        # 결함 버전 재현용 (apply_mutant)
        "file_sha256": mutant.file_sha256,
        "start": mutant.start,
        "end": mutant.end,
        "original": mutant.original,
        "replacement": mutant.replacement,
    }


def _tree_digest(root: Path) -> str:
    return hashlib.sha256(json.dumps(hash_tree(root), sort_keys=True).encode()).hexdigest()


def _select(
    ordered: list[Mutant],
    cap: int,
    run_one: Callable[[Mutant], dict],
    executor: ThreadPoolExecutor,
    batch_size: int,
    accept: Callable[[Mutant, dict], bool],
    outcomes: Counter,
) -> None:
    """순서대로 배치 실행하며 함수당 cap개까지 채택한다. accept가 False를 돌려주면 전체 중단."""
    per_count: Counter = Counter()
    pending = iter(ordered)
    while True:
        batch = []
        for m in pending:
            if per_count[m.method] < cap:
                batch.append(m)
            if len(batch) == batch_size:
                break
        if not batch:
            return
        for m, result in zip(batch, executor.map(run_one, batch), strict=True):
            if per_count[m.method] >= cap:
                outcomes["over_cap"] += 1  # 같은 배치에서 상한을 넘긴 몫 (실행은 했지만 버림)
                continue
            kind = classify(result)
            outcomes[kind] += 1
            if kind == "killed":
                per_count[m.method] += 1
                if not accept(m, result):
                    return


def inject(
    target_root: Path | None = None,
    out_dir: Path | None = None,
    limit: int | None = None,
    caps: dict[str, int] | None = None,
    llm_candidates: list[Mutant] | None = None,
    workers: int = config.MUTGEN_WORKERS,
    seed: int = config.SEED,
    timeout: float | None = None,
) -> dict:
    """결함 버전을 만들어 out_dir에 mutants.jsonl과 summary.json으로 저장하고 요약을 돌려준다.

    limit: 채택 개수 상한 (None이면 후보 전체 검사). caps: 주입 방식별 함수당 상한.
    llm_candidates: LLM 주입 후보 (None이면 저장된 LLM 응답에서 만든다. 없으면 AST만).
    """
    target_root = Path(target_root or config.TARGET_ROOT)
    out_dir = Path(out_dir or config.MUTANTS_DIR)
    caps = caps or config.MUTANT_CAPS
    started = time.monotonic()
    before = hash_tree(target_root)

    if llm_candidates is None:
        from sr_agent.datagen.llm_inject import load_candidates

        llm_candidates, _ = load_candidates(target_root)
    sources = {"ast": list_mutants(target_root), "llm": llm_candidates}
    all_candidates = [m for ms in sources.values() for m in ms]
    texts = {f: read_source(target_root / f) for f in {m.file for m in all_candidates}}
    accepted: list[dict] = []
    outcomes: Counter = Counter()

    def accept(m: Mutant, result: dict) -> bool:
        accepted.append(to_record(m, result, texts[m.file]))
        return limit is None or len(accepted) < limit

    with tempfile.TemporaryDirectory(prefix="mutgen-") as tmp:
        pool: Queue[Workspace] = Queue()
        for i in range(workers):
            pool.put(prepare_workspace(f"mutgen-{i}", source=target_root, runs_dir=Path(tmp)))

        def run_one(m: Mutant) -> dict:
            ws = pool.get()
            try:
                return evaluate_mutant(ws, m, texts[m.file], timeout)
            finally:
                pool.put(ws)

        ws0 = pool.get()
        baseline = run_tests(ws0, "patched", timeout=timeout)
        pool.put(ws0)
        if "error" in baseline or baseline["failed"]:
            return {"error": "원본 테스트가 통과하지 않음", "baseline": baseline}

        with ThreadPoolExecutor(workers) as executor:
            for inject_type, candidates in sources.items():
                if limit is not None and len(accepted) >= limit:
                    break
                _select(order_for_sampling(candidates, seed), caps.get(inject_type, 0), run_one, executor,
                        workers * 2, accept, outcomes)

    all_methods = sorted({m.method for m in all_candidates})
    covered = sorted({r["method"] for r in accepted})
    per_method = Counter(r["method"] for r in accepted)
    summary = {
        "seed": seed,
        "limit": limit,
        "caps": caps,
        "target_digest": _tree_digest(target_root),
        "target_unchanged": compare_hashes(before, hash_tree(target_root))["unchanged"],
        "candidates": len(all_candidates),
        "candidates_by_inject_type": {k: len(v) for k, v in sources.items()},
        "candidates_by_operator": dict(sorted(Counter(m.operator for m in all_candidates).items())),
        "evaluated": sum(outcomes.values()),
        "outcomes": dict(sorted(outcomes.items())),
        "accepted": len(accepted),
        "accepted_by_inject_type": dict(sorted(Counter(r["inject_type"] for r in accepted).items())),
        "accepted_by_operator": dict(sorted(Counter(r["operator"] for r in accepted).items())),
        "methods_with_candidates": len(all_methods),
        "methods_covered": len(covered),
        "mutants_per_method": {str(k): v for k, v in sorted(Counter(per_method.values()).items())},  # 결함 수: 함수 수
        "methods_uncovered": [m for m in all_methods if m not in covered] if limit is None else None,
        "elapsed_s": round(time.monotonic() - started, 1),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / MUTANTS_FILENAME, "w", encoding="utf-8", newline="\n") as f:
        for record in accepted:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    (out_dir / SUMMARY_FILENAME).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="결함 버전 생성·선별 (PRD F2-1, F2-2)")
    parser.add_argument("--limit", type=int, default=None, help="채택 개수 상한 (기본: 전체)")
    parser.add_argument("--workers", type=int, default=config.MUTGEN_WORKERS)
    parser.add_argument("--out-dir", type=Path, default=config.MUTANTS_DIR)
    args = parser.parse_args()
    summary = inject(out_dir=args.out_dir, limit=args.limit, workers=args.workers)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if "error" not in summary:
        print(f"저장: {args.out_dir / MUTANTS_FILENAME}")


if __name__ == "__main__":
    main()
