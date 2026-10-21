"""OpenAI Responses API 호출 공통부.

클라이언트는 인자 없이 만든다 (키는 진입점에서 load_dotenv()로 올린 환경 변수에서 읽음).
응답은 status·거부 여부를 확인한 뒤에만 쓰고, usage를 dict로 정리해 돌려준다.
"""

import time
from dataclasses import dataclass

from openai import OpenAI
from pydantic import BaseModel

from sr_agent import config


class LLMError(RuntimeError):
    """응답을 쓸 수 없음 (incomplete, 파싱 실패 등)."""


class LLMRefusal(LLMError):
    """모델이 거부함. 해당 건은 사람 검토로 넘긴다."""


class LLMQuotaExceeded(RuntimeError):
    """API 사용 한도(크레딧) 소진. 다른 티켓도 모두 실패하므로 끝난 것으로 기록하지 않고 멈춘다.

    충전 후 같은 티켓·배치를 다시 실행하면 저장된 단계부터 이어서 진행한다.
    """


def is_quota_error(error: Exception) -> bool:
    """크레딧·사용 한도 소진(429 insufficient_quota). 일시적인 속도 제한(rate limit)과 구분한다."""
    code = getattr(error, "code", None)
    return code in ("insufficient_quota", "credit_balance_exhausted") or "insufficient_quota" in str(error)


@dataclass(frozen=True)
class ParsedResponse:
    parsed: BaseModel
    usage: dict
    latency_s: float
    response_id: str


def get_client() -> OpenAI:
    """기본 클라이언트. 타임아웃·재시도는 config에서 가져온다."""
    return OpenAI(timeout=config.LLM_TIMEOUT_S, max_retries=config.LLM_MAX_RETRIES)


def usage_dict(usage) -> dict:
    """응답 usage를 로그용 dict로 (입력·캐시·출력·추론 토큰)."""
    if usage is None:
        return {}
    in_details = getattr(usage, "input_tokens_details", None)
    out_details = getattr(usage, "output_tokens_details", None)
    return {
        "input_tokens": usage.input_tokens,
        "cached_tokens": getattr(in_details, "cached_tokens", 0) or 0,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": getattr(out_details, "reasoning_tokens", 0) or 0,
        "total_tokens": usage.total_tokens,
    }


def _refusal(response) -> str | None:
    for item in response.output or []:
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) == "refusal":
                return content.refusal
    return None


def parse_structured(
    client: OpenAI,
    *,
    model: str,
    instructions: str,
    input: str,
    text_format: type[BaseModel],
    effort: str,
    max_output_tokens: int = config.MAX_OUTPUT_TOKENS,
) -> ParsedResponse:
    """structured outputs로 호출해 pydantic 객체를 돌려준다. 쓸 수 없는 응답은 예외."""
    started = time.monotonic()
    response = client.responses.parse(
        model=model,
        instructions=instructions,
        input=input,
        text_format=text_format,
        reasoning={"effort": effort},
        max_output_tokens=max_output_tokens,
    )
    latency = round(time.monotonic() - started, 2)
    usage = usage_dict(response.usage)
    if response.status == "incomplete":
        raise LLMError(f"incomplete: {response.incomplete_details}")
    if refusal := _refusal(response):
        raise LLMRefusal(refusal)
    if response.output_parsed is None:
        raise LLMError(f"구조화 결과 없음 (status={response.status})")
    return ParsedResponse(response.output_parsed, usage, latency, response.id)
