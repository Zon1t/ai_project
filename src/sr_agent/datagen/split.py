"""티켓 데이터셋 조립과 분할 (PRD F2-4). LLM을 쓰지 않는다.

drafts.jsonl(F2-3)과 mutants.jsonl(F2-1)에서 tickets.csv와 split.json을 만든다.
- 분할 단위는 결함(mutant_id). 같은 결함의 티켓 2건은 항상 같은 분할에 둔다.
- 시연 시나리오 대상 함수(config.DEMO_METHODS)의 결함은 학습(train)에만 둔다.
- E2E 홀드아웃(holdout)은 서로 다른 함수에서 결함 하나씩 config.E2E_HOLDOUT_SIZE개.
- 나머지는 연산자별 비율을 유지해 train:test = 8:2.
- 스택트레이스는 제품 코드 traceback을 만들 수 있는 결함의 QA 티켓(B)에만 붙인다.

실행: python -m sr_agent.datagen.split  (PYTHONPATH=src)
"""

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from sr_agent import config
from sr_agent.datagen.tickets import find_leaks, load_jsonl
from sr_agent.datagen.traces import first_traceback

SPLITS = ("train", "test", "holdout")
COLUMNS = [
    "ticket_id", "mutant_id", "title", "body", "stacktrace", "has_stacktrace", "reporter",
    "fixed_method", "inject_type", "operator", "n_failed", "split",
]
_REPORTERS = {"ticket_a": ("A", "운영"), "ticket_b": ("B", "QA")}


def assign_splits(
    mutants: list[dict],
    seed: int = config.SEED,
    demo_methods: tuple[str, ...] = config.DEMO_METHODS,
    holdout_size: int = config.E2E_HOLDOUT_SIZE,
    test_ratio: float = config.SPLIT_TEST_RATIO,
) -> dict[str, str]:
    """mutant_id → train|test|holdout. 같은 입력·시드면 같은 결과."""
    rng = random.Random(seed)
    mutants = sorted(mutants, key=lambda m: m["mutant_id"])
    splits = {m["mutant_id"]: "train" for m in mutants if m["method"] in demo_methods}
    rest = [m for m in mutants if m["mutant_id"] not in splits]

    by_method: dict[str, list[dict]] = defaultdict(list)
    for m in rest:
        by_method[m["method"]].append(m)
    methods = sorted(by_method)
    rng.shuffle(methods)
    if len(methods) < holdout_size:
        raise ValueError(f"홀드아웃 {holdout_size}개를 서로 다른 함수에서 뽑을 수 없음 (함수 {len(methods)}개)")
    for method in methods[:holdout_size]:
        splits[rng.choice(by_method[method])["mutant_id"]] = "holdout"

    by_operator: dict[str, list[dict]] = defaultdict(list)
    for m in rest:
        if m["mutant_id"] not in splits:
            by_operator[m["operator"]].append(m)
    for operator in sorted(by_operator):
        group = by_operator[operator]
        rng.shuffle(group)
        n_test = round(len(group) * test_ratio)
        for i, m in enumerate(group):
            splits[m["mutant_id"]] = "test" if i < n_test else "train"
    return splits


def build_rows(drafts: list[dict], mutants: list[dict], splits: dict[str, str]) -> list[dict]:
    """status=ok인 초안마다 티켓 2행. 분할이 정해진 결함만 포함한다."""
    by_id = {m["mutant_id"]: m for m in mutants}
    rows = []
    for d in sorted(drafts, key=lambda d: d["mutant_id"]):
        if d["status"] != "ok" or d["mutant_id"] not in splits:
            continue
        m = by_id[d["mutant_id"]]
        traceback = first_traceback(m["traces"]) or ""
        for key, (suffix, reporter) in _REPORTERS.items():
            stacktrace = traceback if key == "ticket_b" else ""
            rows.append({
                "ticket_id": f"T-{m['mutant_id'].removeprefix('MUT-')}-{suffix}",
                "mutant_id": m["mutant_id"],
                "title": d[key]["title"],
                "body": d[key]["body"],
                "stacktrace": stacktrace,
                "has_stacktrace": bool(stacktrace),
                "reporter": reporter,
                "fixed_method": m["method"],
                "inject_type": m["inject_type"],
                "operator": m["operator"],
                "n_failed": m["n_failed"],
                "split": splits[m["mutant_id"]],
            })
    return rows


def check_leakage(rows: list[dict], demo_methods: tuple[str, ...] = config.DEMO_METHODS) -> list[str]:
    """누수·무결성 위반 목록 (비어 있어야 정상)."""
    problems = []
    split_of: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        split_of[r["mutant_id"]].add(r["split"])
    problems += [f"{mid}: 여러 분할에 걸침 {sorted(s)}" for mid, s in split_of.items() if len(s) > 1]
    problems += [f"{r['ticket_id']}: 알 수 없는 분할 {r['split']}" for r in rows if r["split"] not in SPLITS]
    problems += [
        f"{r['ticket_id']}: 시연 함수 결함이 {r['split']} 분할에 있음"
        for r in rows if r["fixed_method"] in demo_methods and r["split"] != "train"
    ]
    problems += [
        f"{r['ticket_id']}: 제목·본문에 식별자 {leaks}"
        for r in rows if (leaks := find_leaks(f"{r['title']}\n{r['body']}", r["fixed_method"]))
    ]
    duplicated = [tid for tid, n in Counter(r["ticket_id"] for r in rows).items() if n > 1]
    problems += [f"{tid}: ticket_id 중복" for tid in duplicated]
    return problems


def summarize(rows: list[dict], splits: dict[str, str]) -> dict:
    by_split = defaultdict(Counter)
    for r in rows:
        by_split[r["split"]]["tickets"] += 1
        by_split[r["split"]]["with_stacktrace"] += r["has_stacktrace"]
    for split in splits.values():
        by_split[split]["mutants"] += 1
    holdout_methods = sorted({r["fixed_method"] for r in rows if r["split"] == "holdout"})
    return {
        "seed": config.SEED,
        "counts": {s: dict(by_split[s]) for s in SPLITS},
        "holdout_methods": holdout_methods,
        "mutants": {s: sorted(mid for mid, v in splits.items() if v == s) for s in SPLITS},
    }


def run_split(
    drafts_path: Path | None = None,
    mutants_path: Path | None = None,
    csv_path: Path | None = None,
    split_path: Path | None = None,
    seed: int = config.SEED,
) -> dict:
    """tickets.csv와 split.json을 쓴다. 누수 검사에 걸리면 쓰지 않고 오류를 돌려준다."""
    drafts = load_jsonl(Path(drafts_path or config.TICKET_DRAFTS_PATH))
    mutants = load_jsonl(Path(mutants_path or config.MUTANTS_PATH))
    ok_ids = {d["mutant_id"] for d in drafts if d["status"] == "ok"}
    splits = assign_splits([m for m in mutants if m["mutant_id"] in ok_ids], seed=seed)
    rows = build_rows(drafts, mutants, splits)
    if problems := check_leakage(rows):
        return {"error": "누수 검사 실패", "problems": problems[:20], "n_problems": len(problems)}

    csv_path = Path(csv_path or config.TICKETS_CSV_PATH)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    summary = summarize(rows, splits)
    Path(split_path or config.SPLIT_PATH).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n",
    )
    return {k: v for k, v in summary.items() if k != "mutants"}


def main() -> None:
    parser = argparse.ArgumentParser(description="티켓 데이터셋 조립·분할 (PRD F2-4)")
    parser.parse_args()
    print(json.dumps(run_split(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
