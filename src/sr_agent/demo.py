"""시연 케이스 (data/demo_cases.json): 실시간 모드에서 발표자가 직접 판단하는 티켓.

- A~C: 홀드아웃 결함(학습·이력 KB에 없음)을 심은 작업본으로 처리한다. E2E와 같이 결함 때문에 실패하는 기존 테스트는
  숨기고, 정답 구현으로 판정한다. 숨긴 테스트는 판정에 쓰지 않고 발표자가 판단한 뒤 "정답 확인"에서만 돌린다.
- D: 정답 구현이 없는 새 티켓 (대상 프로젝트에 남아 있는 보안 약점). 담당자 판단이 최종이다.
에이전트에는 티켓 제목·본문·대상 함수만 넘어간다 (answer_* 필드는 넘기지 않는다).
"""

import json
from pathlib import Path

from sr_agent import config
from sr_agent.datagen.tickets import load_jsonl
from sr_agent.ml.corpus import load_tickets
from sr_agent.tools.workspace import WorkspaceError, open_workspace, remove_tree
from sr_agent.workflow.state import JudgeSpec, Ticket, TicketState

DEMO_PATH = config.DATA_DIR / "demo_cases.json"
_REPORTER = "A"  # 운영 담당자 관점 티켓 (스택트레이스 없음)


def demo_cases() -> list[dict]:
    return json.loads(DEMO_PATH.read_text(encoding="utf-8")) if DEMO_PATH.is_file() else []


def find_case(case_id: str) -> dict:
    case = next((c for c in demo_cases() if c["case_id"] == case_id), None)
    if case is None:
        raise ValueError(f"시연 케이스 없음: {case_id}")
    return case


def _mutant_ticket(case: dict) -> tuple[Ticket, dict]:
    from sr_agent.e2e import build_ticket

    mutant = next((m for m in load_jsonl(config.MUTANTS_PATH) if m["mutant_id"] == case["mutant_id"]), None)
    row = next((r for r in load_tickets() if r["mutant_id"] == case["mutant_id"]
                and r["ticket_id"].endswith(f"-{_REPORTER}")), None)
    if mutant is None or row is None:
        raise ValueError(f"결함 데이터 없음: {case['mutant_id']}")
    ticket = build_ticket(mutant, row, _REPORTER).model_copy(update={
        "ticket_id": case["ticket_id"], "scenario": f"시연 {case['case_id']}", "demo_case": case["case_id"],
        "answer_method": mutant["method"]})
    return ticket, mutant


def prepare_demo(case_id: str, runs_dir: Path | None = None, reset: bool = False) -> Ticket:
    """케이스의 티켓을 만들고 작업본을 준비한다. reset이면 작업본·상태를 지우고 결함 버전부터 다시 만든다."""
    from sr_agent.e2e import prepare

    case = find_case(case_id)
    runs_dir = Path(runs_dir or config.RUNS_DIR)
    if reset and (runs_dir / case["ticket_id"]).exists():
        remove_tree(runs_dir / case["ticket_id"])
    if "mutant_id" not in case:
        return Ticket(ticket_id=case["ticket_id"], type=case["type"], title=case["title"], body=case["body"],
                      method=case.get("method"), scenario=f"시연 {case_id}", demo_case=case_id)
    ticket, mutant = _mutant_ticket(case)
    prepared = prepare(ticket, mutant, runs_dir)  # 결함 심기 + 실패 테스트 숨김 + 정답 구현 (이미 있으면 그대로)
    hidden = prepared.judge.hidden_tests
    # 숨긴 테스트는 판정에서 빼고 정답 확인용으로만 둔다 (E2E와 같은 조건)
    return prepared.model_copy(update={"judge": prepared.judge.model_copy(update={"hidden_tests": []}),
                                       "answer_tests": hidden})


def check_answer(state: TicketState, runs_dir: Path | None = None) -> dict:
    """정답 확인: 실제 결함 함수, 에이전트 판단, 숨긴 정답 테스트를 마지막 작업본에서 돌린 결과.

    patch_ok = 판정 통과 + 숨긴 정답 테스트 통과 (승인하는 것이 맞는 패치). 정답이 없는 케이스는 None.
    """
    from sr_agent.workflow.judge import hidden_tests_check

    t = state.ticket
    agent = state.analysis.final_method if state.analysis else t.method
    out = {"answer_method": t.answer_method, "agent_method": agent, "tests": None, "patch_ok": None}
    if not t.answer_tests:
        return out
    try:
        ws = open_workspace(t.ticket_id, runs_dir)
    except WorkspaceError:
        return {**out, "tests": {"passed": None, "detail": "작업본이 없어 확인할 수 없음"}}
    check = hidden_tests_check(ws, JudgeSpec(method=t.answer_method or agent or "", hidden_tests=t.answer_tests))
    out["tests"] = {"passed": check.passed, "detail": check.detail}
    out["patch_ok"] = bool(state.passed and check.passed)
    return out


def judgement_matches(state: TicketState) -> bool | None:
    """담당자 결정(승인·반려)이 정답 확인 결과와 맞는가. 결정 전이거나 정답이 없으면 None."""
    answer = state.answer_check or {}
    if state.decision is None or answer.get("patch_ok") is None:
        return None
    return (state.decision.status == "approved") == answer["patch_ok"]
