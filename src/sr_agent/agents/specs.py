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


def analysis_input(ticket: Ticket, feedback: str | None = None, signal: dict | None = None,
                   candidates: list[dict] | None = None) -> str:
    text = f"[티켓 {ticket.ticket_id}] 유형: {TYPE_LABELS[ticket.type]}\n제목: {ticket.title}\n본문: {ticket.body}"
    if candidates:
        text += ("\n\n[모델 예측 Top-3 (참고 자료, 이미 실행함)]\n"
                 + json.dumps(candidates, ensure_ascii=False, indent=1))
    if signal and signal.get("low"):
        text += ("\n\n[예측 신호: 낮음] 이 티켓은 결함 위치 모델이 학습한 티켓 분포 밖이다 ("
                 + "; ".join(signal["reasons"]) + "). 업무와 무관하거나 증상이 모호할 수 있으니, "
                 "티켓 증상과 원인 함수를 근거로 잇지 못하면 confident=false로 멈춘다.")
    if feedback:
        text += f"\n\n[이전 분석의 문제]\n{feedback}"
    return text


def fix_input(ticket: Ticket, analysis: AnalysisOutput | None, previous: list[FixAttempt]) -> str:
    payload = {
        "ticket": {"id": ticket.ticket_id, "type": TYPE_LABELS[ticket.type], "title": ticket.title,
                   "body": ticket.body},
        "analysis": {"method": analysis.final_method, "reason": analysis.reason,
                     "repro_test": analysis.repro_test_path, "history_ids": analysis.history_ids}
        if analysis else {"method": ticket.method, "note": "진단 결과가 알려 준 대상 함수 (분석 단계 없음)"},
        "search_rules_top_k": config.SEARCH_RULES_TOP_K[ticket.type],
    }
    if previous:
        payload["previous_attempts"] = [
            {"number": a.number, "diff": a.diff[:2000], "error": a.error,
             "failed_checks": [c.model_dump() for c in a.judge.failures()] if a.judge else [],
             "attack_findings": [{"kind": f.kind, "angle": f.angle, "method": f.method, "input": f.input_json,
                                  "expected": f.expected_json, "patched": f.patched, "original": f.original,
                                  "rule_id": f.rule_id, "explanation": f.explanation}
                                 for f in a.attack.verified()] if a.attack else []}
            for a in previous
        ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def attack_input(ticket: Ticket, analysis: AnalysisOutput | None, fix: FixOutput | None, diff: str,
                 angle: tuple[str, str, str] | None = None, round_no: int = 1, rounds: int = 1,
                 context: dict | None = None) -> str:
    """공격 에이전트 입력: 티켓, 결함 위치, 패치 설명, 근거 규칙 본문, diff, 이번 라운드의 관점.
    정답 구현(판정 명세)은 넣지 않는다.

    근거 규칙은 수정 에이전트가 인용한 것과 분석 에이전트가 근거로 찾은 것을 모두 넣는다
    (수정이 규칙 일부만 반영했을 때 나머지 규칙으로 미달을 찾을 수 있게).
    """
    rules = {r["rule_id"]: r for r in load_rules()}
    cited = list(dict.fromkeys([*(fix.rule_ids if fix else []),
                                *(e.ref for e in analysis.evidence if e.kind == "rule")] if analysis else
                               [*(fix.rule_ids if fix else [])]))
    payload = {
        "ticket": {"id": ticket.ticket_id, "type": ticket.type, "title": ticket.title, "body": ticket.body},
        "analysis": ({"method": analysis.final_method, "reason": analysis.reason} if analysis
                     else {"method": ticket.method}),
        "fix": {"method": fix.method, "patch_summary": fix.patch_summary} if fix else None,
        "rules": [{"rule_id": rid, "title": rules[rid]["title"], "description": rules[rid]["description"]}
                  for rid in cited if rid in rules],
        "diff": diff[:4000],
    }
    if angle:
        payload["angle"] = {"name": angle[0], "label": angle[1], "instruction": angle[2],
                            "round": f"{round_no}/{rounds}"}
    if context:  # 미리 가져온 코드·이력 (같은 도구를 다시 부르지 않게)
        payload["context"] = context
    return json.dumps(payload, ensure_ascii=False, indent=2)


def prediction_context(ws, prediction: list[dict]) -> list[dict]:
    """분석 입력에 넣을 모델 예측 Top-k와 후보 함수 코드 (분석이 predict·read_method를 다시 부르지 않게)."""
    from sr_agent.tools.code import read_method

    rows = []
    for rank, p in enumerate(prediction, 1):
        code = read_method(ws, p["method"])
        rows.append({"rank": rank, "method": p["method"],
                     "reasons": [r.get("label", r.get("feature")) for r in p.get("reasons", [])],
                     "code": code[:config.PREFETCH_CODE_CHARS] if isinstance(code, str) else ""})
    return rows
