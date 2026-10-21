"""E2E 배치 (F7-2): 결함 작업본 준비(실패 테스트 숨김), 숨은 정답 테스트 판정, 결과 집계·보고서."""

import json

import pytest

from sr_agent import config
from sr_agent.e2e import (
    build_ticket,
    case_result,
    cited_ids,
    failing_tests,
    hidden_result,
    hide_tests,
    holdout_cases,
    original_function,
    prepare,
    render,
    summarize,
)
from sr_agent.tools.code import apply_patch
from sr_agent.tools.execution import run_tests
from sr_agent.tools.workspace import open_workspace
from sr_agent.workflow.graph import RunOptions
from sr_agent.workflow.judge import hidden_tests_check, judge_defect
from sr_agent.workflow.state import (
    AnalysisOutput,
    FixAttempt,
    FixOutput,
    JudgeCheck,
    JudgeResult,
    TicketState,
)


def test_holdout_cases_one_ticket_per_mutant():
    cases = holdout_cases("A")
    assert len(cases) == config.E2E_HOLDOUT_SIZE
    assert len({m["mutant_id"] for _, m in cases}) == config.E2E_HOLDOUT_SIZE
    assert all(t.ticket_id.endswith("-A") and t.type == "defect" and t.judge is None for t, _ in cases)
    assert len(holdout_cases("B", limit=2)) == 2


def test_build_ticket_appends_stacktrace():
    mutant = {"mutant_id": "MUT-abcd1234"}
    t = build_ticket(mutant, {"title": "제목", "body": "본문", "stacktrace": "Traceback ..."}, "B")
    assert t.ticket_id == "E2E-abcd1234-B" and t.body == "본문\n\n[스택트레이스]\nTraceback ..."
    assert build_ticket(mutant, {"title": "t", "body": "b", "stacktrace": ""}, "A").body == "b"


def test_hide_tests_removes_functions(bug_ws):
    hide_tests(bug_ws.work, ["tests/test_calc.py::test_payable"])
    text = (bug_ws.work / "tests/test_calc.py").read_text(encoding="utf-8")
    assert "def test_payable" not in text and "def test_doubled" in text


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    """실제 홀드아웃 결함 하나 (쿠폰 유형 검증 raise 삭제)."""
    ticket, mutant = holdout_cases("A")[0]
    runs = tmp_path_factory.mktemp("e2e")
    return prepare(ticket, mutant, runs), mutant, open_workspace(ticket.ticket_id, runs)


def test_prepare_hides_failing_tests_and_sets_reference(prepared):
    ticket, mutant, ws = prepared
    spec = ticket.judge
    assert spec.method == mutant["method"] and spec.hidden_tests
    assert failing_tests(ws, "original") == []  # 숨긴 뒤 보이는 테스트는 모두 통과 (S1과 같은 출발점)
    assert spec.reference.code.startswith("def " + mutant["method"].split(".")[-1])
    assert spec.reference.code == original_function(mutant)
    assert (ws.root / "e2e_judge.json").is_file()


def test_hidden_tests_judge_pass_and_fail(prepared):
    ticket, mutant, ws = prepared
    assert hidden_tests_check(ws, ticket.judge).passed is False  # 결함 그대로
    assert apply_patch(ws, mutant["method"], ticket.judge.reference.code)["applied"]
    check = hidden_tests_check(ws, ticket.judge)
    assert check.passed is True and check.data["failed"] == 0
    result = judge_defect(ws, mutant["method"], [], ticket.judge)
    names = {c.name: c.passed for c in result.checks}
    assert names["hidden_tests_pass"] is True and names["existing_tests_pass"] is True
    assert names["repro_original_fails"] is False  # 재현 테스트가 없으면 통과 못함
    assert run_tests(ws, "patched")["failed"] == []


def _state(solved=True, location="coupon.create_coupon"):
    judge = JudgeResult(passed=solved, checks=[JudgeCheck(name="hidden_tests_pass", passed=solved, detail="")])
    analysis = AnalysisOutput(final_method=location, model_top3=[], model_rank=None, reason="", disagreement="",
                              evidence=[], history_ids=[], repro_test_path="repro_tests/test_x.py")
    return TicketState(ticket=holdout_cases("A")[0][0], status="approval_pending" if solved else "human_review",
                       analysis=analysis, prediction=[{"method": "x.y"}, {"method": "coupon.create_coupon"}],
                       attempts=[FixAttempt(number=1, judge=judge)], run_ids=["r1"],
                       human_review_reason=None if solved else "판정 실패")


def test_case_result_and_summary(tmp_path):
    (tmp_path / "r1.jsonl").write_text(json.dumps({"kind": "llm", "tokens": {
        "input_tokens": 1000, "cached_tokens": 800, "output_tokens": 100, "total_tokens": 1100}}) + "\n",
        encoding="utf-8")
    mutant = {"mutant_id": "MUT-1", "method": "coupon.create_coupon", "inject_type": "ast", "operator": "RSD"}
    ok = case_result(_state(), mutant, tmp_path, 12.3)
    assert ok["solved"] and ok["model_top3_hit"] and ok["model_rank"] == 2 and ok["agent_location_correct"]
    assert ok["passed_at_attempt"] == 1 and ok["tokens"] == 1100
    assert ok["cost_usd"] == round((200 * 1.25 + 800 * 0.125 + 100 * 10) / 1e6, 4)
    bad = case_result(_state(False, "coupon.other"), {**mutant, "inject_type": "llm"}, tmp_path, None)
    assert not bad["solved"] and not bad["agent_location_correct"] and bad["reason"] == "판정 실패"
    summary = summarize([ok, bad])
    assert summary["solved"] == 1 and summary["solved_rate"] == 0.5 and summary["agent_location_rate"] == 0.5
    assert summary["by_inject_type"] == {"ast": {"n": 1, "solved_rate": 1.0}, "llm": {"n": 1, "solved_rate": 0.0}}
    assert summary["seconds_mean"] == 12.3 and summary["passed_at_attempt"]["1"] == 1
    md = render("t", summary, [ok, bad], RunOptions(use_attack=False))
    assert "**1/2 (50%)**" in md and "공격 단계 끔" in md and "E2E-00594a87-A" in md


def test_hidden_tests_after_judge_count_regression_leaks(tmp_path):
    """숨긴 테스트는 판정 뒤 따로 돌린다. 판정은 통과했는데 숨긴 테스트가 실패하면 회귀 누출."""
    mutant = {"mutant_id": "MUT-1", "method": "coupon.create_coupon", "inject_type": "ast", "operator": "RSD"}
    state = _state()
    state.attempts[0].fix = FixOutput(method="coupon.create_coupon", rule_ids=["BIZ-CPN-02"], history_ids=[],
                                      patch_summary="하한 적용 (BIZ-CPN-02, HIS-dc2217ee). DEP-DT-01에 따라 교체")
    assert cited_ids(state) == ["BIZ-CPN-02", "HIS-dc2217ee", "DEP-DT-01"]
    leak = case_result(state, mutant, tmp_path, 1.0, {"passed": False, "detail": "실패 1"})
    clean = case_result(state, mutant, tmp_path, 1.0, {"passed": True, "detail": ""})
    failed = case_result(_state(False), mutant, tmp_path, 1.0, None)
    assert leak["regression_leak"] and leak["hidden_tests_pass"] is False and leak["cited"]
    assert not clean["regression_leak"] and failed["hidden_tests_pass"] is None and failed["cited"] is None
    summary = summarize([leak, clean, failed])
    assert summary["solved"] == 2 and summary["hidden_pass"] == 1 and summary["regression_leaks"] == 1
    assert summary["citation_rate"] == 1.0  # 패치가 없는 티켓은 인용률 분모에서 뺀다
    md = render("t", summary, [leak, clean, failed], RunOptions())
    assert "❌ 누출" in md and "| 회귀 누출 (판정 통과 후 숨긴 테스트 실패) | 1건 |" in md


def test_hidden_result_uses_saved_or_legacy_check(tmp_path):
    ticket, mutant = holdout_cases("A")[0]
    assert hidden_result(_state(False), ticket, mutant, tmp_path) is None  # 판정 미통과
    legacy = hidden_result(_state(), ticket, mutant, tmp_path)  # Step 4: 숨긴 테스트가 판정 조건이던 실행
    assert legacy == {"passed": True, "detail": ""}
    saved = tmp_path / ticket.ticket_id / "hidden_check.json"
    saved.parent.mkdir(parents=True)
    saved.write_text(json.dumps({"passed": False, "detail": "x"}), encoding="utf-8")
    assert hidden_result(_state(), ticket, mutant, tmp_path) == {"passed": False, "detail": "x"}
