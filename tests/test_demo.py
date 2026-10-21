"""시연 케이스 (data/demo_cases.json): 결함 작업본 준비, 정답 숨김, 정답 확인과 담당자 판단 일치."""

import pytest

from sr_agent import demo
from sr_agent.agents.specs import analysis_input, fix_input
from sr_agent.tools.code import apply_patch
from sr_agent.tools.workspace import open_workspace
from sr_agent.workflow.state import Decision, FixAttempt, JudgeCheck, JudgeResult, TicketState


def test_cases_file_is_complete():
    cases = demo.demo_cases()
    assert [c["case_id"] for c in cases] == ["A", "B", "C", "D"]
    for c in cases:
        assert c["ticket_id"].startswith("DEMO-") and c["check"] and c["answer_note"] and c["goal"]
        assert ("mutant_id" in c) != ("method" in c)  # A~C 결함 케이스, D 정답 없는 새 티켓


@pytest.fixture(scope="module")
def case_a(tmp_path_factory):
    runs = tmp_path_factory.mktemp("demo_runs")
    return demo.prepare_demo("A", runs_dir=runs), runs


def test_prepare_mutant_case_hides_answer_from_agent_and_judge(case_a):
    ticket, runs = case_a
    assert ticket.ticket_id == "DEMO-A" and ticket.demo_case == "A" and ticket.type == "defect"
    assert ticket.answer_method == "payment.mask_card_number" and ticket.answer_tests
    assert ticket.judge.hidden_tests == [] and ticket.judge.reference is not None  # 판정은 정답 구현으로만
    ws = open_workspace("DEMO-A", runs)
    assert '- "*"' in (ws.work / "src/order_settlement/payment.py").read_text(encoding="utf-8")  # 결함 버전
    for text in (analysis_input(ticket), fix_input(ticket, None, [])):  # 에이전트 입력에 정답이 없다
        assert "mask_card_number" not in text and ticket.answer_tests[0] not in text


def _state(ticket, passed: bool, decision: str | None = None) -> TicketState:
    judge = JudgeResult(passed=passed, checks=[JudgeCheck(name="existing_tests_pass", passed=passed, detail="")])
    d = Decision(status=decision, decided_at="t") if decision else None
    return TicketState(ticket=ticket, status="approval_pending" if passed else "human_review",
                       attempts=[FixAttempt(number=1, judge=judge)], decision=d)


def test_check_answer_before_and_after_fix(case_a):
    ticket, runs = case_a
    broken = demo.check_answer(_state(ticket, passed=True), runs_dir=runs)
    assert broken["tests"]["passed"] is False and broken["patch_ok"] is False  # 결함이 그대로면 승인하면 안 됨

    ws = open_workspace("DEMO-A", runs)
    assert apply_patch(ws, "payment.mask_card_number", ticket.judge.reference.code)["applied"]
    state = _state(ticket, passed=True, decision="approved")
    state.answer_check = demo.check_answer(state, runs_dir=runs)
    assert state.answer_check["tests"]["passed"] is True and state.answer_check["patch_ok"] is True
    assert demo.judgement_matches(state) is True
    state.decision = Decision(status="rejected", reason="x", decided_at="t")
    assert demo.judgement_matches(state) is False
    state.decision = None
    assert demo.judgement_matches(state) is None


def test_reset_rebuilds_defect_version(case_a):
    ticket, runs = case_a
    again = demo.prepare_demo("A", runs_dir=runs, reset=True)
    assert again.answer_tests == ticket.answer_tests
    assert '- "*"' in (open_workspace("DEMO-A", runs).work / "src/order_settlement/payment.py").read_text(
        encoding="utf-8")


def test_case_without_answer(tmp_path):
    ticket = demo.prepare_demo("D", runs_dir=tmp_path)
    assert ticket.type == "security" and ticket.method == "customer_dao.find_by_email"
    assert ticket.judge is None and ticket.answer_tests == []
    answer = demo.check_answer(_state(ticket, passed=True, decision="approved"), runs_dir=tmp_path)
    assert answer["tests"] is None and answer["patch_ok"] is None
    with pytest.raises(ValueError, match="시연 케이스 없음"):
        demo.prepare_demo("Z", runs_dir=tmp_path)
