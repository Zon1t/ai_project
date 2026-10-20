"""에이전트 루프 (F5 공통, F7-1): 도구 호출 왕복, 허용 목록, 상한, 거부·incomplete, fallback, 로그. LLM은 가짜."""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from openai.types.responses import ResponseFunctionToolCall
from pydantic import BaseModel

from sr_agent import config
from sr_agent.agents.context import BudgetExceeded, RunContext
from sr_agent.agents.loop import AgentSpec, AgentStopped, run_agent
from sr_agent.agents.tooling import AGENT_TOOLS, REGISTRY, Tool, execute
from sr_agent.llm import LLMError, LLMRefusal

PRD_TOOLS = {"predict_fault_location", "search_history", "get_callers", "get_coverage", "search_rules",
             "read_method", "apply_patch", "write_repro_test", "run_tests", "run_diff", "property_attack",
             "attack_memory", "check_deprecated", "check_security", "check_style"}


class Answer(BaseModel):
    method: str


USAGE = SimpleNamespace(input_tokens=100, output_tokens=20, total_tokens=120,
                        input_tokens_details=SimpleNamespace(cached_tokens=40),
                        output_tokens_details=SimpleNamespace(reasoning_tokens=5))


def call(name, arguments, call_id="call_1"):
    return ResponseFunctionToolCall(type="function_call", name=name, arguments=json.dumps(arguments),
                                    call_id=call_id, id=f"fc_{call_id}", status="completed")


def response(output=None, parsed=None, status="completed", refusal=None):
    if output is None:
        item = SimpleNamespace(type="refusal", refusal=refusal) if refusal else SimpleNamespace(type="output_text")
        content = [item]
        output = [SimpleNamespace(type="message", content=content)]
    return SimpleNamespace(status=status, output=output, output_parsed=parsed, usage=USAGE,
                           incomplete_details={"reason": "max_output_tokens"})


class FakeClient:
    """parse 호출을 기록하고, 미리 넣어 둔 응답(또는 예외)을 차례로 돌려준다."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.responses = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls.append(json.loads(json.dumps(kwargs, default=str)))
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def spec(**kw):
    return AgentSpec(name="analysis", instructions="고정 지시문", tools=AGENT_TOOLS["analysis"], output_model=Answer,
                     effort="high", **kw)


@pytest.fixture
def ctx(ws, tmp_path):
    return RunContext.new("SR-TEST", ws=ws, runs_dir=tmp_path / "runs")


def _log(ctx):
    return [json.loads(line) for line in ctx.log.path.read_text(encoding="utf-8").splitlines()]


def test_tool_call_round_trip(ctx):
    client = FakeClient(response([call("read_method", {"method": "calc.add"})]),
                        response(parsed=Answer(method="calc.add")))
    result = run_agent(client, spec(), "티켓 본문", ctx)
    assert result.output == Answer(method="calc.add") and result.turns == 2 and result.model == config.MODEL
    assert result.tool_calls == [{"name": "read_method", "arguments": '{"method": "calc.add"}', "error": False}]
    second = client.calls[1]["input"]
    assert second[0] == {"role": "user", "content": "티켓 본문"}
    assert second[1]["type"] == "function_call" and second[1]["call_id"] == "call_1"
    assert second[2]["type"] == "function_call_output" and second[2]["call_id"] == "call_1"
    assert "def add(a, b):" in second[2]["output"]
    assert result.usage["total_tokens"] == 240 and ctx.budget.used == 240
    kinds = [(r["kind"], r.get("tool")) for r in _log(ctx)]
    assert kinds == [("llm", None), ("tool", "read_method"), ("llm", None)]
    assert _log(ctx)[0]["tokens"]["cached_tokens"] == 40


def test_request_follows_api_rules(ctx):
    client = FakeClient(response(parsed=Answer(method="x")))
    run_agent(client, spec(), "t", ctx)
    req = client.calls[0]
    assert req["model"] == config.MODEL and req["reasoning"] == {"effort": "high"}
    assert req["max_output_tokens"] == config.MAX_OUTPUT_TOKENS and req["tool_choice"] == "auto"
    assert req["instructions"] == "고정 지시문" and req["prompt_cache_key"] == "sr-agent-analysis"
    assert "temperature" not in req
    names = [t["name"] for t in req["tools"]]
    assert names == [n for n in AGENT_TOOLS["analysis"] if n in REGISTRY]
    for t in req["tools"]:
        assert t["strict"] is True and t["parameters"]["additionalProperties"] is False
        assert t["parameters"]["required"] == list(t["parameters"]["properties"])


def test_disallowed_tool_and_bad_arguments_are_returned_as_errors(ctx):
    client = FakeClient(
        response([call("apply_patch", {"method": "calc.add"}, "c1"), call("read_method", {"wrong": 1}, "c2")]),
        response(parsed=Answer(method="x")),
    )
    result = run_agent(client, spec(), "t", ctx)
    outputs = [i["output"] for i in client.calls[1]["input"] if i.get("type") == "function_call_output"]
    assert "쓸 수 없음" in outputs[0] and "인자는" in outputs[1]
    assert [c["error"] for c in result.tool_calls] == [True, True]


def test_max_turns(ctx):
    replies = [response([call("read_method", {"method": "calc.add"}, f"c{i}")]) for i in range(3)]
    with pytest.raises(AgentStopped):
        run_agent(FakeClient(*replies), spec(max_turns=3), "t", ctx)


def test_token_budget(ws, tmp_path):
    ctx = RunContext.new("SR-TEST", ws=ws, runs_dir=tmp_path, token_limit=200)
    client = FakeClient(response([call("read_method", {"method": "calc.add"})]), response(parsed=Answer(method="x")))
    with pytest.raises(BudgetExceeded):
        run_agent(client, spec(), "t", ctx)
    assert len(client.calls) == 2 and ctx.budget.used == 240


@pytest.mark.parametrize(("reply", "error"), [
    (response(refusal="도울 수 없습니다"), LLMRefusal),
    (response(status="incomplete"), LLMError),
    (response(parsed=None), LLMError),
])
def test_refusal_and_incomplete(ctx, reply, error):
    with pytest.raises(error):
        run_agent(FakeClient(reply), spec(), "t", ctx)


def _server_error():
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return openai.InternalServerError("server error", response=httpx.Response(500, request=request), body=None)


def test_fallback_on_server_error(ctx):
    client = FakeClient(_server_error(), response(parsed=Answer(method="x")))
    result = run_agent(client, spec(fallback_model="fallback-model"), "t", ctx)
    assert [c["model"] for c in client.calls] == [config.MODEL, "fallback-model"]
    assert result.model == "fallback-model"
    assert [r["kind"] for r in _log(ctx)] == ["fallback", "llm"]


def test_no_fallback_by_default_or_for_client_errors(ctx):
    assert config.MODEL_FALLBACK is None
    with pytest.raises(openai.InternalServerError):
        run_agent(FakeClient(_server_error()), spec(), "t", ctx)
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    bad = openai.BadRequestError("bad", response=httpx.Response(400, request=request), body=None)
    with pytest.raises(openai.BadRequestError):
        run_agent(FakeClient(bad), spec(fallback_model="fallback-model"), "t", ctx)


def test_execute_applies_defaults_and_catches_exceptions(ws):
    seen = {}

    def fake(query, top_k):
        seen.update(query=query, top_k=top_k)
        raise RuntimeError("boom")
    tool = Tool("fake_tool", "d", {"query": {"type": "string"}, "top_k": {"type": ["integer", "null"]}}, fake,
                defaults=(("top_k", 3),))
    REGISTRY["fake_tool"] = tool
    try:
        result = execute("fake_tool", '{"query": "q", "top_k": null}', {"fake_tool"}, ws)
    finally:
        del REGISTRY["fake_tool"]
    assert seen == {"query": "q", "top_k": 3} and result == {"error": "RuntimeError: boom"}
    assert "JSON" in execute("read_method", "{bad", {"read_method"}, ws)["error"]
    assert "작업본" in execute("read_method", '{"method": "calc.add"}', {"read_method"}, None)["error"]


def test_agent_tool_lists_follow_prd():
    assert set().union(*AGENT_TOOLS.values()) <= PRD_TOOLS
    assert set(REGISTRY) <= PRD_TOOLS
    assert "apply_patch" not in AGENT_TOOLS["analysis"] and "predict_fault_location" not in AGENT_TOOLS["fix"]


def test_long_tool_output_is_truncated(ctx, monkeypatch):
    monkeypatch.setattr(config, "TOOL_OUTPUT_CHARS", 50)
    client = FakeClient(response([call("read_method", {"method": "calc.next_day"})]),
                        response(parsed=Answer(method="x")))
    run_agent(client, spec(), "t", ctx)
    output = client.calls[1]["input"][-1]["output"]
    assert "생략" in output and len(output) < 80
