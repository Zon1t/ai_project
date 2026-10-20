"""공격 단계 (F5-3, F4-3): 공격 에이전트 실행, 발견을 코드로 다시 확인, 확인된 공격을 기억.

verified는 코드로 확인된 사실만 뜻한다.
- 미달: 그 입력에서 수정본 결과가 에이전트가 근거 규칙으로 계산한 기대값과 다르다.
- 회귀: 그 입력에서 수정본 결과가 원본과 다르다.
기대값 자체는 에이전트의 주장이므로 통과·실패는 판정 노드(참조 구현 비교)가 정한다.
"""

import json

from openai import OpenAI

from sr_agent.agents.context import RunContext
from sr_agent.agents.loop import AgentStopped, run_agent
from sr_agent.agents.specs import ATTACK, attack_input
from sr_agent.llm import LLMError, LLMRefusal
from sr_agent.tools.attack import attack_memory
from sr_agent.tools.execution import run_diff
from sr_agent.tools.workspace import Workspace
from sr_agent.workflow.state import (
    AnalysisOutput,
    AttackFinding,
    AttackReport,
    FixAttempt,
    Ticket,
    VerifiedFinding,
)


def _expected_matches(expected, result: dict) -> bool:
    if isinstance(expected, dict) and set(expected) == {"$exception"}:
        return not result.get("ok") and str(result.get("exception", "")).startswith(str(expected["$exception"]))
    return bool(result.get("ok")) and result.get("value") == expected


def verify_finding(ws: Workspace, finding: AttackFinding) -> VerifiedFinding:
    """발견 하나를 run_diff로 다시 실행해 확인한다."""
    out = VerifiedFinding(**finding.model_dump())
    try:
        inp = json.loads(finding.input_json)
        expected = json.loads(finding.expected_json)
    except json.JSONDecodeError as e:
        out.check = f"JSON 파싱 실패: {e}"
        return out
    result = run_diff(ws, finding.method, [inp])
    if isinstance(result, dict):
        out.check = f"실행 실패: {result['error']}"
        return out
    row = result[0]
    out.original, out.patched = row["original"], row["patched"]
    if finding.kind == "under_fix":
        out.verified = not _expected_matches(expected, row["patched"])
        out.check = "수정본이 규칙상 기대값과 다름" if out.verified else "수정본이 기대값과 같음 (미달 아님)"
    else:
        out.verified = not row["same"]
        out.check = "수정본이 원본과 다름" if out.verified else "원본과 같음 (회귀 아님)"
    return out


def run_attack(client: OpenAI, ticket: Ticket, analysis: AnalysisOutput | None, attempt: FixAttempt,
               ctx: RunContext) -> AttackReport:
    """공격 에이전트를 돌리고 발견을 확인한다. 공격 실패(반복 상한·응답 오류)는 기록만 하고 판정으로 넘어간다."""
    report = AttackReport()
    try:
        result = run_agent(client, ATTACK, attack_input(ticket, analysis, attempt.fix, attempt.diff), ctx)
    except LLMRefusal:
        raise  # 거부는 사람 검토로
    except (AgentStopped, LLMError) as e:
        report.error = f"{type(e).__name__}: {e}"
        return report
    out = result.output
    report.summary, report.attempts = out.summary, out.attempts
    report.findings = [verify_finding(ctx.ws, f) for f in out.findings]
    for f in report.verified():
        saved = attack_memory("save", ticket.type, {
            "ticket_id": ticket.ticket_id, "method": f.method, "kind": f.kind, "input": json.loads(f.input_json),
            "expected": json.loads(f.expected_json), "actual": f.patched, "note": f.explanation,
        })
        report.saved_to_memory += isinstance(saved, list)
    ctx.log.write(step="attack", kind="verify", findings=len(report.findings), verified=len(report.verified()),
                  kinds=[f.kind for f in report.verified()])
    return report
