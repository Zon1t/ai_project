"""에이전트 루프 (CLAUDE.md "OpenAI API 사용 규칙", PRD F5·F7-1).

프레임워크 없이 직접 짠다.
1. responses.parse로 호출한다 (지시문·도구 정의는 고정 텍스트로 앞에, 최종 결과는 structured outputs).
2. 응답의 function_call마다 도구를 실행하고 같은 call_id의 function_call_output으로 돌려준다.
3. function_call이 없으면 output_parsed를 최종 결과로 돌려준다.
- 응답 status가 incomplete면 LLMError, 거부(refusal)면 LLMRefusal → 해당 티켓은 사람 검토.
- 서버 오류·연결 오류·모델 없음이면 (SDK 재시도 후) fallback 모델로 다시 요청한다. 기본은 꺼짐.
- 호출마다 usage를 티켓 토큰 예산에 더하고, LLM 호출·도구 호출을 runs/<run_id>.jsonl에 남긴다.
- 마지막 턴에는 도구를 막고 지금까지 확인한 내용으로 결과를 정리하게 한다 (탐색한 내용을 잃지 않게).
"""

import time
from dataclasses import dataclass, field
from typing import Any

import openai
from openai import OpenAI
from pydantic import BaseModel

from sr_agent import config
from sr_agent.agents.context import RunContext
from sr_agent.agents.tooling import REGISTRY, _summary, execute, to_output
from sr_agent.llm import LLMError, LLMQuotaExceeded, LLMRefusal, _refusal, is_quota_error, usage_dict

_DROP_KEYS = {"parsed", "parsed_arguments"}  # parse()가 붙이는 필드. 다시 입력으로 보내면 API가 거부한다
FINAL_TURN_NOTICE = ("도구 호출 횟수 상한에 도달했다. 더 이상 도구를 부를 수 없다. "
                     "지금까지 확인한 내용으로 최종 결과를 정리하라.")


class AgentStopped(RuntimeError):
    """반복 상한까지 최종 결과를 내지 못함."""


@dataclass(frozen=True)
class AgentSpec:
    """에이전트 정의. instructions와 도구 목록은 실행마다 바뀌지 않는다 (prompt caching)."""

    name: str
    instructions: str
    tools: tuple[str, ...]
    output_model: type[BaseModel]
    effort: str
    model: str = config.MODEL
    fallback_model: str | None = None
    max_turns: int = config.AGENT_MAX_TURNS


@dataclass
class AgentResult:
    output: BaseModel
    model: str
    turns: int
    tool_calls: list[dict] = field(default_factory=list)  # [{"name", "arguments", "error"}]
    usage: dict = field(default_factory=dict)


def _clean(value: Any) -> Any:
    """응답 항목을 다음 요청의 입력으로 쓸 수 있게 dict로 바꾼다."""
    if isinstance(value, BaseModel):
        value = value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k not in _DROP_KEYS}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _should_fallback(error: Exception) -> bool:
    if isinstance(error, openai.APIConnectionError):  # APITimeoutError 포함
        return True
    if isinstance(error, openai.APIStatusError):
        code = getattr(error, "code", None)
        return error.status_code >= 500 or error.status_code in (404, 429) or code == "model_not_found"
    return False


def _call(client: OpenAI, spec: AgentSpec, model: str, items: list, tools: list[dict], ctx: RunContext,
          turn: int) -> tuple[Any, str]:
    """한 번 호출. 실패하면 fallback 모델로 한 번 더. 반환: (응답, 실제로 쓴 모델)."""
    ctx.budget.check()
    kwargs = {
        "instructions": spec.instructions,
        "input": items,
        "text_format": spec.output_model,
        "reasoning": {"effort": spec.effort},
        "max_output_tokens": config.MAX_OUTPUT_TOKENS,
        "prompt_cache_key": f"sr-agent-{spec.name}",
    }
    if tools:
        kwargs.update(tools=tools, tool_choice="none" if turn == spec.max_turns else "auto")
    started = time.monotonic()
    try:
        try:
            response = client.responses.parse(model=model, **kwargs)
        except Exception as e:
            if not (spec.fallback_model and spec.fallback_model != model and _should_fallback(e)
                    and not is_quota_error(e)):
                raise
            ctx.log.write(step=spec.name, kind="fallback", turn=turn, model=model,
                          fallback_model=spec.fallback_model, error=_summary(f"{type(e).__name__}: {e}"))
            model = spec.fallback_model
            response = client.responses.parse(model=model, **kwargs)
    except openai.APIError as e:
        # SDK 재시도(fallback 포함) 뒤에도 실패: 크레딧 소진은 멈추고, 나머지(타임아웃·5xx 등)는 이 티켓만 사람 검토로
        ctx.log.write(step=spec.name, kind="api_error", turn=turn, model=model,
                      error=_summary(f"{type(e).__name__}: {e}"))
        if is_quota_error(e):
            raise LLMQuotaExceeded("OpenAI API 크레딧(사용 한도) 소진 — 충전 후 같은 티켓을 다시 실행하면 이어서 "
                                   "진행합니다") from e
        raise LLMError(f"API 호출 실패 ({type(e).__name__}): {_summary(str(e))}") from e
    usage = usage_dict(response.usage)
    calls = [o.name for o in response.output or [] if getattr(o, "type", None) == "function_call"]
    ctx.log.write(step=spec.name, kind="llm", turn=turn, model=model, status=response.status, tokens=usage,
                  latency_s=round(time.monotonic() - started, 2),
                  budget_used=ctx.budget.used + usage.get("total_tokens", 0),
                  output_summary=", ".join(calls) if calls else "final")
    ctx.budget.add(usage)
    if response.status == "incomplete":
        raise LLMError(f"incomplete: {response.incomplete_details}")
    if refusal := _refusal(response):
        raise LLMRefusal(refusal)
    return response, model


def run_agent(client: OpenAI, spec: AgentSpec, user_input: str, ctx: RunContext) -> AgentResult:
    """도구를 호출하며 최종 구조화 결과가 나올 때까지 반복한다."""
    allowed = {n for n in spec.tools if n in REGISTRY}
    tool_schemas = [REGISTRY[n].schema() for n in spec.tools if n in REGISTRY]
    items: list = [{"role": "user", "content": user_input}]
    model = spec.model
    result = AgentResult(output=None, model=model, turns=0)  # type: ignore[arg-type]
    total: dict[str, int] = {}
    for turn in range(1, spec.max_turns + 1):
        if turn == spec.max_turns and turn > 1:
            items.append({"role": "user", "content": FINAL_TURN_NOTICE})
        response, model = _call(client, spec, model, items, tool_schemas, ctx, turn)
        for k, v in usage_dict(response.usage).items():
            total[k] = total.get(k, 0) + v
        calls = [o for o in response.output or [] if getattr(o, "type", None) == "function_call"]
        if not calls:
            if response.output_parsed is None:
                raise LLMError(f"구조화 결과 없음 (status={response.status})")
            result.output, result.model, result.turns, result.usage = response.output_parsed, model, turn, total
            return result
        items.extend(_clean(o) for o in response.output)
        for call in calls:
            started = time.monotonic()
            value = execute(call.name, call.arguments, allowed, ctx.ws)
            error = isinstance(value, dict) and set(value) == {"error"}
            ctx.log.write(step=spec.name, kind="tool", turn=turn, tool=call.name, input=_summary(call.arguments),
                          output_summary=_summary(value), error=error, latency_s=round(time.monotonic() - started, 2))
            result.tool_calls.append({"name": call.name, "arguments": call.arguments, "error": error})
            items.append({"type": "function_call_output", "call_id": call.call_id, "output": to_output(value)})
    raise AgentStopped(f"{spec.name}: {spec.max_turns}회 안에 최종 결과를 내지 못함")
