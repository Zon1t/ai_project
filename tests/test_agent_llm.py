"""실제 API 최소 호출: 도구 1개(read_method)로 에이전트 루프와 fallback이 동작하는지. 비용 발생 → `pytest -m llm`."""

import json

import pytest
from dotenv import load_dotenv
from pydantic import BaseModel

from sr_agent import config, llm
from sr_agent.agents.context import RunContext
from sr_agent.agents.loop import AgentSpec, run_agent
from sr_agent.tools.workspace import prepare_workspace

pytestmark = pytest.mark.llm

INSTRUCTIONS = (
    "너는 코드 조회 도우미다. read_method 도구로 함수 소스를 읽고 질문에 답한다. "
    "추측하지 말고 반드시 도구로 코드를 확인한 뒤 답한다."
)


class Answer(BaseModel):
    variable: str  # 쿠폰 최소 주문 금액 판단에 넘기는 변수 이름
    line: str  # 그 판단이 있는 코드 한 줄


@pytest.fixture(scope="module")
def client():
    load_dotenv(config.PROJECT_ROOT / ".env")
    return llm.get_client()


@pytest.fixture
def ctx(tmp_path):
    ws = prepare_workspace("SR-LLM", runs_dir=tmp_path / "ws")
    return RunContext.new("SR-LLM", ws=ws, runs_dir=tmp_path / "runs")


def _spec(**kw):
    return AgentSpec(name="probe", instructions=INSTRUCTIONS, tools=("read_method",), output_model=Answer,
                     effort="low", **kw)


QUESTION = "discount.apply_coupon에서 쿠폰 적용 가능 여부(is_coupon_applicable)에 어떤 금액 변수를 넘기나?"


def test_minimal_tool_call(client, ctx):
    result = run_agent(client, _spec(), QUESTION, ctx)
    assert [c["name"] for c in result.tool_calls] == ["read_method"]
    assert result.output.variable == "order_amount"
    assert "is_coupon_applicable" in result.output.line
    log = [json.loads(line) for line in ctx.log.path.read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in log] == ["llm", "tool", "llm"]
    assert all(r["tokens"]["total_tokens"] > 0 for r in log if r["kind"] == "llm")


def test_fallback_when_primary_model_missing(client, ctx):
    result = run_agent(client, _spec(model="gpt-5-does-not-exist", fallback_model=config.MODEL), QUESTION, ctx)
    assert result.model == config.MODEL and result.output.variable == "order_amount"
    log = [json.loads(line) for line in ctx.log.path.read_text(encoding="utf-8").splitlines()]
    assert log[0]["kind"] == "fallback" and log[0]["model"] == "gpt-5-does-not-exist"


# ---------------- 공격 에이전트: 일부러 불완전하게 만든 S1 패치 ----------------

def test_attack_agent_catches_incomplete_s1_patch(client, tmp_path, monkeypatch):
    from sr_agent.run import load_ticket
    from sr_agent.tools.code import apply_patch
    from sr_agent.workflow.attack import run_attack
    from sr_agent.workflow.graph import _work_diff
    from sr_agent.workflow.state import AnalysisOutput, Evidence, FixAttempt, FixOutput
    from tests.test_attack import S1_PATCHES

    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma")  # 공격 메모리 격리
    results = {}
    for patch, cited in (("A_floor_only", ["BIZ-CPN-02"]), ("B_basis_only", ["BIZ-CPN-01"])):
        ws = prepare_workspace(f"SR-ATK-{patch[0]}", runs_dir=tmp_path / "ws")
        assert apply_patch(ws, "discount.apply_coupon", S1_PATCHES[patch])["applied"]
        ctx = RunContext.new(ws.ticket_id, ws=ws, runs_dir=tmp_path / "runs")
        analysis = AnalysisOutput(
            final_method="discount.apply_coupon", model_top3=[], model_rank=2, reason="복수 쿠폰 적용 결함",
            disagreement="", history_ids=[], repro_test_path="repro_tests/test_x.py",
            evidence=[Evidence(kind="rule", ref=r, note="") for r in ("BIZ-CPN-01", "BIZ-CPN-02")])
        attempt = FixAttempt(number=1, fix=FixOutput(method="discount.apply_coupon", patch_summary="쿠폰 결함 수정",
                                                     rule_ids=cited, history_ids=[]), diff=_work_diff(ws))
        report = run_attack(client, load_ticket("SR-2041"), analysis, attempt, ctx)
        results[patch] = report
        print(f"\n[{patch}] {report.summary} (오류: {report.error}, 토큰 {ctx.budget.used})")
        for f in report.findings:
            print(f"  {'O' if f.verified else 'X'} {f.kind} {f.input_json} 기대 {f.expected_json} "
                  f"수정본 {f.patched} 원본 {f.original} [{f.rule_id}] {f.explanation}")
    for patch, report in results.items():
        assert any(f.verified and f.kind == "under_fix" for f in report.findings), patch
