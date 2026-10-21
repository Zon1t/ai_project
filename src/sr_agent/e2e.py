"""E2E 배치 (PRD F7-2, F7-3 준비): 홀드아웃 결함 20건을 사람 개입 없이 처리하고 해결률을 낸다.

티켓마다:
1. 원본 복사본·작업본에 결함을 심는다 (mutants.jsonl).
2. 결함 때문에 실패하는 기존 테스트를 두 복사본에서 숨긴다 → 에이전트는 "기존 테스트는 통과하는데 버그가 있는"
   상황에서 시작한다 (S1과 같은 조건). 숨긴 테스트는 판정만 쓴다 (숨은 정답 테스트).
3. 판정 명세: 숨은 정답 테스트 + 정답 구현 = 결함을 심기 전 원래 함수 (재현 테스트 기대값 검증에 쓴다).
4. 워크플로를 그대로 돌린다 (분석 → 수정 → 공격 → 판정, 재시도 3회).
결과: eval/results/e2e_<태그>.{md,csv,json}. 끝난 티켓은 건너뛰고 이어서 실행한다.
"""

import ast
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sr_agent import config
from sr_agent.datagen.mutate import apply_mutant, read_source
from sr_agent.datagen.tickets import load_jsonl
from sr_agent.ml.corpus import load_tickets
from sr_agent.tools.code import find_function
from sr_agent.tools.execution import run_tests
from sr_agent.tools.workspace import Workspace, prepare_workspace
from sr_agent.workflow.graph import FINAL, RunOptions, run_ticket
from sr_agent.workflow.state import JudgeSpec, ReferenceImpl, Ticket, TicketState

REPORTERS = {"A": "운영", "B": "QA"}


def e2e_ticket_id(mutant_id: str, reporter: str) -> str:
    return f"E2E-{mutant_id.removeprefix('MUT-')}-{reporter}"


def build_ticket(mutant: dict, row: dict, reporter: str) -> Ticket:
    """티켓 데이터셋 한 행 → 워크플로 티켓. 스택트레이스가 있으면 본문 뒤에 붙인다."""
    body = row["body"] + (f"\n\n[스택트레이스]\n{row['stacktrace']}" if row.get("stacktrace") else "")
    return Ticket(ticket_id=e2e_ticket_id(mutant["mutant_id"], reporter), type="defect", title=row["title"],
                  body=body, scenario="E2E")


def original_function(mutant: dict) -> str:
    """결함을 심기 전 원래 함수 소스 (정답 구현)."""
    text = read_source(config.TARGET_ROOT / mutant["file"])
    names = mutant["method"].split(".")[1:]
    node = find_function(ast.parse(text), names)
    start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
    lines = text.splitlines()[start - 1:node.end_lineno]
    indent = len(lines[0]) - len(lines[0].lstrip())
    return "\n".join(line[indent:] for line in lines) + "\n"


def failing_tests(ws: Workspace, version: str = "original") -> list[str]:
    """그 버전에서 실패하는 기존 테스트 전체 (run_tests 요약은 10건까지라 pytest 기록을 직접 읽는다)."""
    result = run_tests(ws, version, [config.TARGET_TEST_SUBDIR], timeout=config.JUDGE_TEST_TIMEOUT_S)
    if "error" in result:
        raise RuntimeError(f"테스트 실행 실패: {result['error']}")
    report = json.loads((ws.reports / f"pytest_{version}.json").read_text(encoding="utf-8"))
    return sorted({r["nodeid"].replace("\\", "/") for r in report["results"] if r["outcome"] == "failed"})


def hide_tests(root: Path, tests: list[str]) -> None:
    """테스트 함수를 파일에서 지운다 (parametrize 사례는 함수째). root는 원본 복사본 또는 작업본."""
    by_file: dict[str, set[str]] = {}
    for nodeid in tests:
        path, _, name = nodeid.partition("::")
        by_file.setdefault(path, set()).add(name.split("[")[0])
    for rel, names in by_file.items():
        path = root / rel
        text = path.read_bytes().decode("utf-8")
        tree = ast.parse(text)
        lines = text.splitlines(keepends=True)
        spans = [(n.decorator_list[0].lineno if n.decorator_list else n.lineno, n.end_lineno)
                 for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        for start, end in sorted(spans, reverse=True):
            del lines[start - 1:end]
        path.write_bytes("".join(lines).encode("utf-8"))


def prepare(ticket: Ticket, mutant: dict, runs_dir: Path) -> Ticket:
    """결함 버전 작업본을 만들고 실패 테스트를 숨긴 뒤, 판정 명세를 붙인 티켓을 돌려준다. 이미 있으면 그대로."""
    ws = prepare_workspace(ticket.ticket_id, runs_dir=runs_dir)
    spec_path = ws.root / "e2e_judge.json"
    if spec_path.is_file():
        return ticket.model_copy(update={"judge": JudgeSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))})
    for root in (ws.original, ws.work):
        apply_mutant(root, mutant)
    hidden = failing_tests(ws, "original")
    if not hidden:
        raise RuntimeError(f"{mutant['mutant_id']}: 결함 버전에서 실패하는 테스트가 없음")
    for root in (ws.original, ws.work):
        hide_tests(root, hidden)
    if leftover := failing_tests(ws, "original"):
        raise RuntimeError(f"{mutant['mutant_id']}: 숨긴 뒤에도 실패하는 테스트 {leftover[:3]}")
    spec = JudgeSpec(method=mutant["method"], hidden_tests=hidden,
                     reference=ReferenceImpl(method=mutant["method"], code=original_function(mutant)))
    spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    return ticket.model_copy(update={"judge": spec})


def holdout_cases(reporter: str = "A", limit: int | None = None) -> list[tuple[Ticket, dict]]:
    """홀드아웃 결함마다 (티켓, 결함). 결함 ID 순."""
    mutants = {m["mutant_id"]: m for m in load_jsonl(config.MUTANTS_PATH)}
    rows = [r for r in load_tickets() if r["split"] == "holdout" and r["ticket_id"].endswith(f"-{reporter}")]
    cases = [(build_ticket(mutants[r["mutant_id"]], r, reporter), mutants[r["mutant_id"]])
             for r in sorted(rows, key=lambda r: r["mutant_id"])]
    return cases[:limit] if limit else cases


def _cost(tokens: dict) -> float:
    p = config.PRICE_PER_MTOK
    fresh = tokens.get("input_tokens", 0) - tokens.get("cached_tokens", 0)
    return (fresh * p["input"] + tokens.get("cached_tokens", 0) * p["cached"]
            + tokens.get("output_tokens", 0) * p["output"]) / 1_000_000


def _usage(state: TicketState, runs_dir: Path) -> dict:
    total: dict[str, int] = {}
    for run_id in state.run_ids:
        log = runs_dir / f"{run_id}.jsonl"
        if not log.is_file():
            continue
        for line in log.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("kind") == "llm":
                for k, v in r["tokens"].items():
                    total[k] = total.get(k, 0) + v
    return total


def case_result(state: TicketState, mutant: dict, runs_dir: Path, seconds: float | None) -> dict:
    """티켓 한 건의 결과 행."""
    true_method = mutant["method"]
    predicted = [p["method"] for p in state.prediction[:3]]
    usage = _usage(state, runs_dir)
    passed_at = next((a.number for a in state.attempts if a.judge and a.judge.passed), None)
    reports = [a.attack for a in state.attempts if a.attack]
    found = [f.angle for r in reports for f in r.verified()]
    return {
        "ticket_id": state.ticket.ticket_id, "mutant_id": mutant["mutant_id"], "method": true_method,
        "inject_type": mutant["inject_type"], "operator": mutant["operator"],
        "status": state.status, "solved": state.status == "approval_pending" and state.passed,
        "model_top3_hit": true_method in predicted if predicted else None,
        "model_rank": predicted.index(true_method) + 1 if true_method in predicted else None,
        "agent_location_correct": state.analysis.final_method == true_method if state.analysis else False,
        "agent_method": state.analysis.final_method if state.analysis else None,
        "attempts": len(state.attempts), "passed_at_attempt": passed_at,
        "attack_rounds": sum(len(r.rounds) for r in reports),
        "found_angles": ",".join(dict.fromkeys(found)),  # 확인된 발견을 낸 공격 관점
        "tokens": usage.get("total_tokens", 0), "cost_usd": round(_cost(usage), 4),
        "seconds": round(seconds, 1) if seconds is not None else None,
        "reason": state.human_review_reason or "",
    }


def run_case(ticket: Ticket, mutant: dict, runs_dir: Path, client, options: RunOptions, restart: bool) -> dict:
    state_path = runs_dir / ticket.ticket_id / config.STATE_FILENAME
    done = TicketState.load(state_path)
    if done is not None and done.status in FINAL and not restart:
        return case_result(done, mutant, runs_dir, None)
    if restart and (runs_dir / ticket.ticket_id).exists():
        import shutil

        shutil.rmtree(runs_dir / ticket.ticket_id)
    started = time.monotonic()
    prepared = prepare(ticket, mutant, runs_dir)
    state = run_ticket(prepared, client, runs_dir=runs_dir, options=options,
                       progress=lambda m: print(f"  [{ticket.ticket_id}] {m}", flush=True))
    return case_result(state, mutant, runs_dir, time.monotonic() - started)


def summarize(rows: list[dict]) -> dict:
    def rate(values):
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values), 3) if values else None

    def group(key):
        return {k: {"n": len(g), "solved_rate": rate([r["solved"] for r in g])}
                for k in sorted({r[key] for r in rows}) for g in [[r for r in rows if r[key] == k]]}

    timed = [r["seconds"] for r in rows if r["seconds"] is not None]
    return {
        "n": len(rows),
        "solved": sum(r["solved"] for r in rows),
        "solved_rate": rate([r["solved"] for r in rows]),
        "model_top3_rate": rate([r["model_top3_hit"] for r in rows]),
        "agent_location_rate": rate([r["agent_location_correct"] for r in rows]),
        "mean_attempts": rate([r["attempts"] for r in rows]),
        "passed_at_attempt": {str(k): sum(r["passed_at_attempt"] == k for r in rows)
                              for k in range(1, 2 + config.MAX_PATCH_RETRIES)},
        "tokens_total": sum(r["tokens"] for r in rows),
        "tokens_mean": round(sum(r["tokens"] for r in rows) / len(rows)) if rows else 0,
        "cost_usd_total": round(sum(r["cost_usd"] for r in rows), 2),
        "seconds_mean": round(sum(timed) / len(timed), 1) if timed else None,
        "by_inject_type": group("inject_type"),
        "attack_rounds_total": sum(r.get("attack_rounds", 0) for r in rows),
        "found_by_angle": {a: sum(a in r.get("found_angles", "").split(",") for r in rows)
                           for a, _, _ in config.ATTACK_ANGLES},
    }


def render(tag: str, summary: dict, rows: list[dict], options: RunOptions) -> str:
    def pct(v):
        return "-" if v is None else f"{v:.0%}"

    predict = "사용" if options.use_predict else "끔"
    attack = "끔" if not options.use_attack else (
        f"사용 (라운드 {options.attack_rounds}회 고정)" if options.attack_rounds is not None
        else f"사용 (라운드 기본 {config.ATTACK_ROUNDS}회, 재시도면 {config.ATTACK_ROUNDS_WITH_HISTORY}회)")
    solved = f"{summary['solved']}/{summary['n']} ({pct(summary['solved_rate'])})"
    lines = [
        f"# E2E 배치: {tag} (F7-2)", "",
        f"- 조건: 예측 도구 {predict}, 공격 단계 {attack}",
        f"- 홀드아웃 결함 {summary['n']}건, 티켓 1건씩 (결함 때문에 실패하는 기존 테스트는 숨기고 판정만 사용)",
        f"- 가격 가정(100만 토큰당): 입력 ${config.PRICE_PER_MTOK['input']}, 캐시 ${config.PRICE_PER_MTOK['cached']}, "
        f"출력 ${config.PRICE_PER_MTOK['output']}", "",
        "| 지표 | 값 |", "|---|---|",
        f"| **해결률** (사람 개입 없이 판정 통과) | **{solved}** |",
        f"| 모델 Top-3 적중 | {pct(summary['model_top3_rate'])} |",
        f"| 에이전트 최종 위치 정답 | {pct(summary['agent_location_rate'])} |",
        f"| 평균 수정 시도 | {summary['mean_attempts']} |",
        f"| 통과한 시도 차수 | {', '.join(f'{k}회째 {v}건' for k, v in summary['passed_at_attempt'].items())} |",
        f"| 토큰 합계 / 평균 | {summary['tokens_total']:,} / {summary['tokens_mean']:,} |",
        f"| 비용 합계 | ${summary['cost_usd_total']} |",
        f"| 평균 소요 시간 | {summary['seconds_mean']}초 |",
        f"| 공격 라운드 합계 | {summary.get('attack_rounds_total', 0)} |",
        "| 확인된 발견을 낸 관점 (티켓 수) | " + (", ".join(
            f"{label} {summary.get('found_by_angle', {}).get(name, 0)}" for name, label, _ in config.ATTACK_ANGLES))
        + " |", "",
        "| 주입 방식 | n | 해결률 |", "|---|---|---|",
        *[f"| {k} | {v['n']} | {pct(v['solved_rate'])} |" for k, v in summary["by_inject_type"].items()], "",
        "## 티켓별", "",
        "| 티켓 | 정답 함수 | 방식 | 결과 | 모델 순위 | 에이전트 위치 | 시도 | 토큰 | 비용 | 시간 | 사유 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['ticket_id']} | `{r['method']}` | {r['inject_type']}/{r['operator']} | "
            f"{'✅' if r['solved'] else '❌ ' + r['status']} | {r['model_rank'] or '-'} | "
            f"{'✅' if r['agent_location_correct'] else '❌ ' + str(r['agent_method'])} | {r['attempts']} | "
            f"{r['tokens']:,} | ${r['cost_usd']} | {r['seconds'] or '-'} | {r['reason'][:60]} |")
    return "\n".join(lines) + "\n"


def run_e2e(tag: str = "full", options: RunOptions | None = None, reporter: str = "A", limit: int | None = None,
            workers: int = 1, restart: bool = False, client=None, runs_tag: str | None = None) -> dict:
    """홀드아웃 배치를 돌리고 결과 파일을 쓴다. 작업본은 runs/e2e/<runs_tag>/ (조건이 같으면 이어서 쓴다)."""
    options = options or RunOptions()
    if client is None:
        from sr_agent import llm

        client = llm.get_client()
    runs_dir = config.RUNS_DIR / "e2e" / (runs_tag or tag)
    cases = holdout_cases(reporter, limit)
    with ThreadPoolExecutor(workers) as executor:
        rows = list(executor.map(lambda c: run_case(c[0], c[1], runs_dir, client, options, restart), cases))
    summary = summarize(rows)
    out = config.EVAL_RESULTS_DIR
    out.mkdir(parents=True, exist_ok=True)
    name = f"e2e_{tag}"
    (out / f"{name}.md").write_text(render(tag, summary, rows, options), encoding="utf-8", newline="\n")
    (out / f"{name}.json").write_text(json.dumps({"tag": tag, "options": options.__dict__, "reporter": reporter,
                                                  "summary": summary, "rows": rows}, ensure_ascii=False, indent=2)
                                      + "\n", encoding="utf-8", newline="\n")
    with open(out / f"{name}.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["ticket_id"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return summary
