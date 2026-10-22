"""공격 에이전트 탐지율 (PRD 2장 KPI): 일부러 불완전하게 만든 패치 10건 중 미달·회귀를 찾은 수.

패치는 사람이 써 둔다 (data/attack_kpi/patches.json). 케이스마다:
1. 작업본 준비: 시연 시나리오(S1~S3)는 대상 프로젝트 원본, 홀드아웃 결함은 E2E와 같게 결함을 심고 실패 테스트를 숨긴다.
2. 불완전 패치 적용 후 코드로 "정말 불완전한지" 확인 (공격 전, LLM 없음):
   변경 범위가 대상 함수뿐, 기존 테스트 통과(그냥 테스트로 잡히는 패치는 공격이 필요 없음),
   사람이 고른 점검 입력(probes)에서 기준과 결과가 다름. 기준 = 정답 구현 (코드 표준화는 원본).
   점검 입력은 공격 에이전트에 주지 않는다.
3. 공격 단계만 실행한다 (화면 실행과 같은 설정: 관점 3라운드, 코드 미리 넣기, 빈 공격 이력).
4. 탐지 = 판정의 "공격 결과 재확인"이 공격이 낸 입력에서 기준과 다른 결과를 하나 이상 찾음 (코드가 정함).
불완전 패치 확인을 통과한 케이스만 앞에서부터 config.ATTACK_KPI_CASES개 쓴다.
"""

import json
import time
from pathlib import Path

from sr_agent import config
from sr_agent.agents.context import RunContext
from sr_agent.tools.attack import compare_inputs
from sr_agent.tools.code import apply_patch
from sr_agent.tools.workspace import Workspace, prepare_workspace, remove_tree
from sr_agent.workflow.attack import attack_context, run_attack
from sr_agent.workflow.graph import _work_diff
from sr_agent.workflow.judge import attack_findings_check, existing_tests_check, scope_check
from sr_agent.workflow.reference import build_reference
from sr_agent.workflow.state import AnalysisOutput, FixAttempt, FixOutput, JudgeSpec, ReferenceImpl, Ticket

PATCHES_PATH = config.DATA_DIR / "attack_kpi" / "patches.json"


def load_patches(path: Path | None = None) -> list[dict]:
    return json.loads(Path(path or PATCHES_PATH).read_text(encoding="utf-8"))


def _baseline(ticket: Ticket) -> str:
    return "original" if ticket.type == "standardize" else "reference"


def prepare_case(case: dict, runs_dir: Path) -> tuple[Ticket, Workspace]:
    """케이스의 티켓과 작업본 (불완전 패치 적용 전). 매번 새로 만든다."""
    from sr_agent import ui
    from sr_agent.e2e import holdout_cases, prepare

    case_dir = runs_dir / case["case_id"]
    if case_dir.exists():
        remove_tree(case_dir)
    scenario = {t.ticket_id: t for t in ui.scenario_tickets()}
    if case["source"] in scenario:
        ticket = scenario[case["source"]].model_copy(update={"ticket_id": case["case_id"]})
        ws = prepare_workspace(ticket.ticket_id, runs_dir=runs_dir, overwrite=True)
        return ticket, ws
    found = holdout_cases(only=[case["source"]])
    if not found:
        raise ValueError(f"홀드아웃 결함 없음: {case['source']}")
    ticket, mutant = found[0]
    ticket = prepare(ticket.model_copy(update={"ticket_id": case["case_id"]}), mutant, runs_dir)
    return ticket, prepare_workspace(ticket.ticket_id, runs_dir=runs_dir)


def _versions(ws: Workspace, ticket: Ticket) -> dict[str, Path]:
    src = config.TARGET_SRC_SUBDIR
    versions = {"original": ws.original / src, "patched": ws.work / src}
    if _baseline(ticket) == "reference":
        versions["reference"] = build_reference(ws, ticket.judge)
    return versions


def check_flawed(case: dict, ticket: Ticket, ws: Workspace) -> dict:
    """불완전 패치를 적용하고 정말 불완전한지 코드로 확인한다. 반환 {"ok", "problems", "probe"}."""
    result = apply_patch(ws, case["method"], case["code"], case.get("imports"))
    if not result.get("applied") or not result.get("compile_ok"):
        return {"ok": False, "problems": [f"패치 적용 실패: {result.get('error')}"]}
    problems = []
    scope = scope_check(ws, case["method"])
    if not scope.passed:
        problems.append(f"변경 범위: {scope.detail}")
    tests = existing_tests_check(ws)
    if not tests.passed:
        problems.append(f"기존 테스트에서 잡힘 (공격 없이도 판정이 막음): {tests.detail}")
    baseline = _baseline(ticket)
    raw = compare_inputs(case["probe_method"], case["probes"], _versions(ws, ticket), baseline, "patched", ws.work)
    if "error" in raw:
        problems.append(f"점검 입력 실행 실패: {raw['error']}")
        probe = None
    else:
        diff = [c for c in raw["cases"] if c["results"][baseline] != c["results"]["patched"]]
        probe = {"inputs": len(raw["cases"]), "mismatches": len(diff),
                 "example": diff[0] if diff else None}
        if not diff:
            problems.append("점검 입력에서 기준과 결과가 같음 (불완전하지 않음)")
    return {"ok": not problems, "problems": problems, "probe": probe}


def _analysis(ticket: Ticket, case: dict) -> AnalysisOutput | None:
    if ticket.type != "defect":
        return None
    return AnalysisOutput(final_method=case["method"], model_top3=[], model_rank=None,
                          reason="티켓 증상의 원인 함수", disagreement="", evidence=[], history_ids=[],
                          repro_test_path="", ticket_symptom="", symptom_check="", confident=True, blocker="")


def attack_case(client, case: dict, ticket: Ticket, ws: Workspace, runs_dir: Path) -> dict:
    """불완전 패치에 공격 단계만 돌리고, 판정의 공격 입력 재실행으로 탐지 여부를 정한다."""
    if ticket.type != "defect":  # 공격 입력이 티켓 유형의 대상 함수를 쓰도록 (분석 단계 없음)
        ticket = ticket.model_copy(update={"method": case["method"]})
    fix = FixOutput(method=case["method"], patch_summary=case["claim"], rule_ids=case["rule_ids"], history_ids=[])
    attempt = FixAttempt(number=1, fix=fix, diff=_work_diff(ws))
    ctx = RunContext.new(ticket.ticket_id, ws=ws, runs_dir=runs_dir)
    started = time.monotonic()
    report = run_attack(client, ticket, _analysis(ticket, case), attempt, ctx, previous=[], focused=True,
                        context=attack_context(ws, ticket, case["method"]))
    spec = ticket.judge if ticket.judge is not None else JudgeSpec(
        method=case["method"], reference=ReferenceImpl(method=case["method"], code=""))
    check = attack_findings_check(ws, report, spec, _baseline(ticket))
    return {
        "detected": check.passed is False,
        "recheck": check.detail,
        "proposed": len(report.findings),
        "verified": len(report.verified()),
        "kinds": sorted({c["kind"] for c in check.data.get("failed_inputs", [])}),
        "rounds": [r.angle for r in report.rounds],
        "tokens": ctx.budget.used,
        "seconds": round(time.monotonic() - started, 1),
    }


def run(client, runs_dir: Path | None = None, verify_only: bool = False) -> dict:
    """케이스마다 불완전 패치 확인 → 공격. 결과를 eval/results/attack_kpi.{json,md}에 쓴다."""
    runs_dir = Path(runs_dir or config.RUNS_DIR / "attack_kpi")
    memory, config.ATTACK_MEMORY_DIR = config.ATTACK_MEMORY_DIR, runs_dir / "memory"  # 빈 공격 이력
    rows = []
    try:
        for case in load_patches():
            if sum(r["used"] for r in rows) >= config.ATTACK_KPI_CASES:
                break
            ticket, ws = prepare_case(case, runs_dir)
            flawed = check_flawed(case, ticket, ws)
            row = {"case_id": case["case_id"], "method": case["method"], "flaw": case["flaw"],
                   "description": case["description"], "flawed_check": flawed, "used": flawed["ok"]}
            print(f"[{case['case_id']}] {case['method']} 불완전 확인: "
                  f"{'통과' if flawed['ok'] else '; '.join(flawed['problems'])}", flush=True)
            if flawed["ok"] and not verify_only:
                row.update(attack_case(client, case, ticket, ws, runs_dir))
                print(f"  → {'탐지' if row['detected'] else '놓침'} ({row['recheck']})", flush=True)
            rows.append(row)
    finally:
        config.ATTACK_MEMORY_DIR = memory
    used = [r for r in rows if r["used"]]
    summary = {"n": len(used), "detected": sum(r.get("detected", False) for r in used),
               "by_flaw": {k: {"n": sum(r["flaw"] == k for r in used),
                               "detected": sum(r["flaw"] == k and r.get("detected", False) for r in used)}
                           for k in ("under_fix", "regression")},
               "tokens": sum(r.get("tokens", 0) for r in used), "verify_only": verify_only}
    if not verify_only:
        write_results(summary, rows)
    return summary


def write_results(summary: dict, rows: list[dict], results_dir: Path | None = None) -> Path:
    out = Path(results_dir or config.EVAL_RESULTS_DIR)
    out.mkdir(parents=True, exist_ok=True)
    (out / "attack_kpi.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False,
                                                    indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    labels = {"under_fix": "미달", "regression": "회귀"}
    rate = summary["detected"] / summary["n"] if summary["n"] else 0
    lines = ["# 공격 에이전트 탐지율 (PRD 2장 KPI)", "",
             f"- 불완전 패치 {summary['n']}건 중 탐지 **{summary['detected']}건 ({rate:.0%})**, 목표 80% 이상",
             "- 탐지 = 판정이 공격 에이전트가 낸 입력을 다시 실행해 정답 구현(표준화는 원본)과 다른 결과를 찾음",
             "- 패치는 사람이 쓰고(data/attack_kpi/patches.json), 공격 전에 코드로 불완전함을 확인 "
             "(변경 범위, 기존 테스트 통과, 점검 입력에서 기준과 다름). 점검 입력은 공격 에이전트에 주지 않음",
             "", "| 케이스 | 함수 | 결함 | 불완전 내용 | 결과 | 판정 재실행 |", "|---|---|---|---|---|---|"]
    for r in rows:
        if not r["used"]:
            continue
        mark = "✅ 탐지" if r.get("detected") else "❌ 놓침"
        lines.append(f"| {r['case_id']} | `{r['method']}` | {labels[r['flaw']]} | {r['description']} | {mark} | "
                     f"{r.get('recheck', '-')} |")
    skipped = [r for r in rows if not r["used"]]
    if skipped:
        lines += ["", "불완전 확인에서 뺀 케이스: " + ", ".join(
            f"{r['case_id']} ({'; '.join(r['flawed_check']['problems'])})" for r in skipped)]
    path = out / "attack_kpi.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path
