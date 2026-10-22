"""접수 검사 (F5-0): 수정 요청으로 볼 수 없는 티켓을 분석 전에 걸러 "보완 요청"으로 돌려보낸다.

1. 코드 검사: 본문 길이·글자 (LLM을 부르지 않음, 화면 등록 때도 같은 검사)
2. LLM 판단: 수정 요청인지, 대상 업무인지, 증상 문장 인용, 기대 동작 (structured outputs)
3. 받아들일지는 코드가 정한다: LLM이 "요청 맞음"이라고 해도 인용한 증상 문장이 실제 티켓에 없으면 받지 않는다.
판정 노드와 별개다 (판정에는 LLM을 넣지 않는다).
"""

import re
from pathlib import Path

import openai
from openai import OpenAI

from sr_agent import config
from sr_agent.agents.context import RunContext
from sr_agent.llm import LLMError, LLMQuotaExceeded, is_quota_error, parse_structured
from sr_agent.workflow.state import IntakeOutput, IntakeResult, Ticket

INSTRUCTIONS = (Path(__file__).resolve().parents[1] / "agents" / "prompts" / "intake.md").read_text(encoding="utf-8")
_TYPE_LABELS = {"defect": "결함 수정", "standardize": "코드 표준화", "security": "보안 진단 조치"}
_SPACE = re.compile(r"\s+")
_EDGE = "\"'“”‘’「」『』.,!?。 "


def normalize(text: str) -> str:
    """인용 비교용: 공백을 하나로, 양끝 따옴표·문장부호 제거."""
    return _SPACE.sub(" ", text).strip().strip(_EDGE)


def quoted_from(quote: str, ticket: Ticket, min_chars: int = config.INTAKE_MIN_QUOTE_CHARS) -> bool:
    """quote가 티켓 제목·본문에 글자 그대로 있고 충분히 긴가 (LLM이 지어낸 증상을 막는다)."""
    q = normalize(quote)
    return len(q.replace(" ", "")) >= min_chars and q in normalize(ticket.text)


def rule_problems(title: str, body: str) -> list[str]:
    """LLM 없이 거르는 조건. 화면에서 등록할 때도 쓴다."""
    compact = _SPACE.sub("", body)
    problems = []
    if len(compact) < config.INTAKE_MIN_BODY_CHARS:
        problems.append(f"본문이 너무 짧음 (공백 제외 {len(compact)}자, 최소 {config.INTAKE_MIN_BODY_CHARS}자): "
                        "무엇이 어떻게 잘못됐는지 적어 주세요")
    elif len(set(compact)) <= 2:
        problems.append("본문이 같은 글자의 반복임")
    return problems


def decide(ticket: Ticket, output: IntakeOutput) -> IntakeResult:
    """LLM 판단을 코드 조건으로 확인해 받아들일지 정한다."""
    problems = []
    if not output.is_change_request:
        problems.append("시스템 수정 요청으로 볼 수 없음")
    if not output.in_domain:
        problems.append("대상 시스템(주문·정산) 업무와 관련 없음")
    if not quoted_from(output.symptom_quote, ticket):
        problems.append("티켓에서 증상(요청) 문장을 찾지 못함")
    if ticket.type == "defect" and not output.expected_behavior.strip():
        problems.append("올바른 동작이 무엇인지 알 수 없음")
    return IntakeResult(accepted=not problems, problems=problems, output=output)


def intake_input(ticket: Ticket) -> str:
    text = f"유형: {_TYPE_LABELS[ticket.type]}\n제목: {ticket.title}\n본문: {ticket.body}"
    if ticket.method:
        text += f"\n대상 함수(진단 결과): {ticket.method}"
    return text


def check_ticket(client: OpenAI, ticket: Ticket, ctx: RunContext) -> IntakeResult:
    """접수 검사. 코드 검사에서 걸리면 LLM을 부르지 않는다. 호출은 실행 로그·토큰 예산에 남긴다."""
    problems = rule_problems(ticket.title, ticket.body)
    if problems:
        result = IntakeResult(accepted=False, problems=problems)
    else:
        ctx.budget.check()
        try:
            parsed = parse_structured(client, model=config.MODEL, instructions=INSTRUCTIONS,
                                      input=intake_input(ticket), text_format=IntakeOutput,
                                      effort=config.INTAKE_REASONING_EFFORT)
        except openai.APIError as e:
            if is_quota_error(e):
                raise LLMQuotaExceeded("OpenAI API 크레딧(사용 한도) 소진 — 충전 후 같은 티켓을 다시 실행하면 이어서 "
                                       "진행합니다") from e
            raise LLMError(f"API 호출 실패 ({type(e).__name__}): {str(e)[:config.LOG_SUMMARY_CHARS]}") from e
        ctx.log.write(step="intake", kind="llm", turn=1, model=config.MODEL, status="completed",
                      tokens=parsed.usage, latency_s=parsed.latency_s, output_summary="final")
        ctx.budget.add(parsed.usage)
        result = decide(ticket, parsed.parsed)
    ctx.log.write(step="intake", kind="intake", accepted=result.accepted, problems=result.problems)
    return result
