"""분석·수정 에이전트 정의와 입력 만들기 (PRD F5-1, F5-2).

에이전트 사이에는 구조화 결과만 넘긴다 (대화 이력 없음). 입력은 티켓과 앞 노드의 결과로 만든다.
"""

import json
from pathlib import Path

from sr_agent import config
from sr_agent.agents.loop import AgentSpec
from sr_agent.agents.tooling import AGENT_TOOLS
from sr_agent.rag.kb import load_rules
from sr_agent.workflow.state import AnalysisOutput, AttackOutput, FixAttempt, FixOutput, Ticket

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

ATTACK = AgentSpec(
    name="attack",
    instructions=(_PROMPTS / "attack.md").read_text(encoding="utf-8"),
    tools=AGENT_TOOLS["attack"],
    output_model=AttackOutput,
    effort=config.AGENT_REASONING_EFFORT["attack"],
    fallback_model=config.MODEL_FALLBACK,
    max_turns=config.ATTACK_MAX_TURNS,
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
             "failed_checks": [c.model_dump() for c in a.judge.failures()] if a.judge else [],
             "attack_findings": [{"kind": f.kind, "input": f.input_json, "expected": f.expected_json,
                                  "patched": f.patched, "rule_id": f.rule_id, "explanation": f.explanation}
                                 for f in a.attack.verified()] if a.attack else []}
            for a in previous
        ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def attack_input(ticket: Ticket, analysis: AnalysisOutput, fix: FixOutput | None, diff: str) -> str:
    """공격 에이전트 입력: 티켓, 결함 위치, 패치 설명, 근거 규칙 본문, diff. 정답 구현(판정 명세)은 넣지 않는다.

    근거 규칙은 수정 에이전트가 인용한 것과 분석 에이전트가 근거로 찾은 것을 모두 넣는다
    (수정이 규칙 일부만 반영했을 때 나머지 규칙으로 미달을 찾을 수 있게).
    """
    rules = {r["rule_id"]: r for r in load_rules()}
    cited = list(dict.fromkeys([*(fix.rule_ids if fix else []),
                                *(e.ref for e in analysis.evidence if e.kind == "rule")]))
    payload = {
        "ticket": {"id": ticket.ticket_id, "type": ticket.type, "title": ticket.title, "body": ticket.body},
        "analysis": {"method": analysis.final_method, "reason": analysis.reason},
        "fix": {"method": fix.method, "patch_summary": fix.patch_summary} if fix else None,
        "rules": [{"rule_id": rid, "title": rules[rid]["title"], "description": rules[rid]["description"]}
                  for rid in cited if rid in rules],
        "diff": diff[:4000],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
