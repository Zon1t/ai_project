"""분석·수정 에이전트 정의와 입력 만들기 (PRD F5-1, F5-2).

에이전트 사이에는 구조화 결과만 넘긴다 (대화 이력 없음). 입력은 티켓과 앞 노드의 결과로 만든다.
"""

import json
from pathlib import Path

from sr_agent import config
from sr_agent.agents.loop import AgentSpec
from sr_agent.agents.tooling import AGENT_TOOLS
from sr_agent.workflow.state import AnalysisOutput, FixAttempt, FixOutput, Ticket

_PROMPTS = Path(__file__).parent / "prompts"
TYPE_LABELS = {"defect": "결함 수정", "standardize": "코드 표준화", "security": "보안 진단 조치"}

ANALYSIS = AgentSpec(
    name="analysis",
    instructions=(_PROMPTS / "analysis.md").read_text(encoding="utf-8"),
    tools=AGENT_TOOLS["analysis"],
    output_model=AnalysisOutput,
    effort=config.AGENT_REASONING_EFFORT["analysis"],
    fallback_model=config.MODEL_FALLBACK,
)

FIX = AgentSpec(
    name="fix",
    instructions=(_PROMPTS / "fix.md").read_text(encoding="utf-8"),
    tools=AGENT_TOOLS["fix"],
    output_model=FixOutput,
    effort=config.AGENT_REASONING_EFFORT["fix"],
    fallback_model=config.MODEL_FALLBACK,
)


def analysis_input(ticket: Ticket, feedback: str | None = None) -> str:
    text = f"[티켓 {ticket.ticket_id}] 유형: {TYPE_LABELS[ticket.type]}\n제목: {ticket.title}\n본문: {ticket.body}"
    if feedback:
        text += f"\n\n[이전 분석의 문제]\n{feedback}"
    return text


def fix_input(ticket: Ticket, analysis: AnalysisOutput, previous: list[FixAttempt]) -> str:
    payload = {
        "ticket": {"id": ticket.ticket_id, "type": TYPE_LABELS[ticket.type], "title": ticket.title,
                   "body": ticket.body},
        "analysis": {"method": analysis.final_method, "reason": analysis.reason,
                     "repro_test": analysis.repro_test_path, "history_ids": analysis.history_ids},
        "search_rules_top_k": config.SEARCH_RULES_TOP_K[ticket.type],
    }
    if previous:
        payload["previous_attempts"] = [
            {"number": a.number, "diff": a.diff[:2000], "error": a.error,
             "failed_checks": [c.model_dump() for c in a.judge.failures()] if a.judge else []}
            for a in previous
        ]
    return json.dumps(payload, ensure_ascii=False, indent=2)
