"""워크플로 그래프 (F5-1 ~ F5-5): 정상 흐름, 재현 확인, 재시도 상한, 이어서 실행, 사람 검토 전환. 에이전트는 가짜."""

import json

import pytest

from sr_agent import config
from sr_agent.agents.loop import AgentResult
from sr_agent.llm import LLMRefusal
from sr_agent.tools.code import apply_patch
from sr_agent.tools.testing import write_repro_test
from sr_agent.workflow import graph
from sr_agent.workflow.state import AnalysisOutput, Evidence, FixOutput, Ticket, TicketState

TICKET = Ticket(ticket_id="SR-BUG", type="defect", title="결제 금액 음수", body="할인이 크면 결제 금액이 음수입니다.")
REPRO = "from order_settlement import calc\n\n\ndef test_never_negative():\n    assert calc.payable(100, 300) == 0\n"
WEAK = "from order_settlement import calc\n\n\ndef test_ok():\n    assert calc.payable(500, 100) == 400\n"
GOOD = "def payable(amount, discount):\n    return max(0, amount - discount)\n"
BAD = "def payable(amount, discount):\n    return 0\n"


def analysis_output(path="repro_tests/test_never_negative.py"):
    return AnalysisOutput(final_method="calc.payable", model_top3=["calc.settle", "calc.payable", "fees.fee"],
                          model_rank=2, reason="0원 하한 없음", disagreement="settle은 payable을 호출할 뿐",
                          evidence=[Evidence(kind="caller", ref="calc.settle", note="payable 호출")],
                          history_ids=["HIS-1"], repro_test_path=path)


class FakeAgents:
    """spec.name별로 미리 정한 행동을 한다. 실제 도구로 작업본을 바꾼다."""

    def __init__(self, repro_codes=(REPRO,), patches=(GOOD,)):
        self.repro_codes = list(repro_codes)
        self.patches = list(patches)
        self.inputs = {"analysis": [], "fix": []}

    def __call__(self, client, spec, user_input, ctx):
        self.inputs[spec.name].append(user_input)
        if spec.name == "analysis":
            code = self.repro_codes.pop(0)
            if isinstance(code, Exception):
                raise code
            name = "never_negative" if code is REPRO else "weak"
            path = write_repro_test(ctx.ws, name, code)["path"]
            ctx.budget.add({"total_tokens": 100})
            return AgentResult(output=analysis_output(path), model="fake", turns=3)
        patch = self.patches.pop(0)
        if isinstance(patch, Exception):
            raise patch
        apply_patch(ctx.ws, "calc.payable", patch)
        ctx.budget.add({"total_tokens": 50})
        return AgentResult(output=FixOutput(method="calc.payable", patch_summary="0원 하한 (BIZ-CPN-02)",
                                            rule_ids=["BIZ-CPN-02"], history_ids=["HIS-1"]), model="fake", turns=2)


@pytest.fixture
def run(bug_target, tmp_path, monkeypatch):
    def _run(agents, **kw):
        monkeypatch.setattr(graph, "run_agent", agents)
        return graph.run_ticket(TICKET, client=None, source=bug_target, runs_dir=tmp_path / "runs", **kw)
    return _run


def test_happy_path(run, tmp_path):
    agents = FakeAgents()
    state = run(agents)
    assert state.status == "approval_pending" and state.repro_verified and state.passed
    assert len(state.attempts) == 1 and state.attempts[0].judge.passed
    assert "+    return max(0, amount - discount)" in state.attempts[0].diff
    assert state.tokens_used == 150
    saved = TicketState.load(tmp_path / "runs" / "SR-BUG" / config.STATE_FILENAME)
    assert saved.status == "approval_pending" and saved.analysis.final_method == "calc.payable"
    log = [json.loads(line) for line in (tmp_path / "runs" / f"{state.run_ids[0]}.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [r["kind"] for r in log] == ["verify", "judge", "end"]
    # 다시 실행하면 끝난 티켓은 그대로 돌려준다 (에이전트 호출 없음)
    again = FakeAgents(repro_codes=[], patches=[])
    assert run(again).status == "approval_pending" and again.inputs == {"analysis": [], "fix": []}


def test_fix_input_has_only_structured_results(run):
    agents = FakeAgents()
    run(agents)
    payload = json.loads(agents.inputs["fix"][0])
    assert payload["analysis"] == {"method": "calc.payable", "reason": "0원 하한 없음",
                                   "repro_test": "repro_tests/test_never_negative.py", "history_ids": ["HIS-1"]}
    assert payload["search_rules_top_k"] == config.SEARCH_RULES_TOP_K["defect"]
    assert "previous_attempts" not in payload


def test_repro_not_failing_on_original_retries_analysis(run):
    agents = FakeAgents(repro_codes=[WEAK, REPRO])
    state = run(agents)
    assert state.status == "approval_pending" and state.analysis_tries == 2
    assert "원본에서 통과함" in agents.inputs["analysis"][1]  # 두 번째 분석에 실패 이유 전달


def test_repro_never_verified_goes_to_human_review(run):
    state = run(FakeAgents(repro_codes=[WEAK, WEAK]))
    assert state.status == "human_review" and "분석 실패" in state.human_review_reason
    assert state.attempts == []


def test_retries_then_human_review(run):
    agents = FakeAgents(patches=[BAD] * (1 + config.MAX_PATCH_RETRIES))
    state = run(agents)
    assert state.status == "human_review" and "재시도 상한" in state.human_review_reason
    assert len(state.attempts) == 1 + config.MAX_PATCH_RETRIES
    retry = json.loads(agents.inputs["fix"][1])["previous_attempts"][0]
    assert retry["number"] == 1 and any(c["name"] == "existing_tests_pass" for c in retry["failed_checks"])


def test_retry_succeeds_on_second_attempt(run):
    state = run(FakeAgents(patches=[BAD, GOOD]))
    assert state.status == "approval_pending" and [a.judge.passed for a in state.attempts] == [False, True]


def test_resume_skips_finished_analysis(run, tmp_path):
    state = run(FakeAgents(patches=[LLMRefusal("거부")]))
    assert state.status == "human_review" and state.repro_verified and "LLMRefusal" in state.human_review_reason
    # 수정 단계에서 끊긴 상태로 되돌린 뒤 다시 실행하면, 분석은 건너뛰고 수정부터 이어서 한다
    path = tmp_path / "runs" / "SR-BUG" / config.STATE_FILENAME
    saved = TicketState.load(path)
    saved.status, saved.human_review_reason = "fixing", None
    saved.save(path)
    second = FakeAgents(repro_codes=[], patches=[GOOD])
    resumed = run(second)
    assert resumed.status == "approval_pending" and second.inputs["analysis"] == []
    assert len(resumed.run_ids) == 2 and resumed.tokens_used == state.tokens_used + 50


def test_non_defect_type_not_supported_yet(run):
    with pytest.raises(NotImplementedError):
        graph.run_ticket(TICKET.model_copy(update={"type": "security"}), client=None)
