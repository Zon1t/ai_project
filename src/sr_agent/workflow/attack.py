"""공격 단계 (F5-3, F4-3): 공격 에이전트 실행, 발견을 코드로 다시 확인, 확인된 공격을 기억.

라운드: 수정 시도 1회마다 관점(config.ATTACK_ANGLES)을 하나씩 맡은 공격을 여러 번 한다.
- 기본 config.ATTACK_ROUNDS회. 이 티켓의 이전 시도가 판정에서 실패했으면(재시도) config.ATTACK_ROUNDS_WITH_HISTORY회.
  focused 재시도(v1.2, 화면 실행 기본)는 문제를 찾았던 관점 + 아직 쓰지 않은 관점으로
  config.ATTACK_RETRY_FOCUSED_ROUNDS회.
  공격 이력은 라운드 수에 쓰지 않는다 (오탐이 섞이면 라운드만 늘어남). 이력은 각 라운드가 먼저 검색해 재사용한다.
- config.ATTACK_PARALLEL개씩 묶어 동시에 실행하고, 한 묶음에서 확인된 발견이 나오면 다음 묶음은 건너뛴다
  (판정이 실패해 어차피 재시도하므로).
- 여러 라운드가 같은 입력을 내면 하나만 남긴다.

공격 이력 저장(save_confirmed)은 판정 뒤에 한다. 판정이 공격 입력을 정답(코드 표준화는 원본)과 다시 비교해
다르다고 확인한 입력만 저장한다 (예외 메시지만 다른 비정상 입력처럼 정답도 같은 결과를 내는 오탐은 저장하지 않음).
정답 구현이 없어 판정이 다시 비교하지 못했으면 코드로 확인된(verified) 발견을 저장한다.

verified는 코드로 확인된 사실만 뜻한다.
- 미달: 그 입력에서 수정본 결과가 에이전트가 근거 규칙으로 계산한 기대값과 다르다.
- 회귀: 그 입력에서 수정본 결과가 원본과 다르다.
기대값 자체는 에이전트의 주장이므로 통과·실패는 판정 노드(참조 구현 비교)가 정한다.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from openai import OpenAI

from sr_agent import config
from sr_agent.agents.context import RunContext
from sr_agent.agents.loop import AgentStopped, run_agent
from sr_agent.agents.specs import ATTACK, attack_input
from sr_agent.llm import LLMError, LLMRefusal
from sr_agent.tools.attack import attack_memory, input_key
from sr_agent.tools.execution import run_diff
from sr_agent.tools.workspace import Workspace
from sr_agent.workflow.state import (
    AnalysisOutput,
    AttackFinding,
    AttackReport,
    AttackRound,
    FixAttempt,
    JudgeResult,
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


def plan_rounds(attempt_number: int, override: int | None = None) -> tuple[int, str]:
    """이번 시도의 공격 라운드 수와 늘린 이유. override(실행 옵션)가 있으면 그 값."""
    if override is not None:
        return max(0, min(override, len(config.ATTACK_ANGLES))), f"실행 옵션 {override}회"
    if attempt_number > 1:
        return min(config.ATTACK_ROUNDS_WITH_HISTORY, len(config.ATTACK_ANGLES)), "이전 시도 판정 실패"
    return min(config.ATTACK_ROUNDS, len(config.ATTACK_ANGLES)), ""


_ANGLES = {a[0]: a for a in config.ATTACK_ANGLES}


def plan_angles(attempt_number: int, previous: list[FixAttempt] = (), override: int | None = None,
                focused: bool = True) -> tuple[list[tuple[str, str, str]], str]:
    """이번 시도에 쓸 관점과 그 이유.

    첫 시도·실행 옵션·focused=False는 plan_rounds대로 앞에서부터 (기본 3, 재시도 5).
    focused 재시도(v1.2): ① 이전 시도에서 판정이 실제 문제로 확인한 입력을 찾은 관점 ② 아직 쓰지 않은 관점
    ③ 나머지 순으로 config.ATTACK_RETRY_FOCUSED_ROUNDS개. 이전 공격 입력은 판정이 어차피 모두 다시 실행한다.
    """
    if override is not None or attempt_number <= 1 or not focused:
        n, why = plan_rounds(attempt_number, override)
        return list(config.ATTACK_ANGLES[:n]), why
    hit: list[str] = []
    for a in previous:
        if a.attack and a.judge:
            hit += [f.angle for f in confirmed_findings(a.attack, a.judge) if f.angle in _ANGLES]
    used = {r.angle for a in previous if a.attack for r in a.attack.rounds}
    unused = [n for n in _ANGLES if n not in used]
    order = list(dict.fromkeys([*hit, *unused, *_ANGLES]))[:config.ATTACK_RETRY_FOCUSED_ROUNDS]

    def label(names: list[str]) -> str:
        return "·".join(_ANGLES[n][1] for n in names)
    found = [n for n in order if n in hit]
    fresh = [n for n in order if n in unused and n not in hit]
    parts = ([f"문제를 찾았던 관점({label(found)})"] if found else []) + \
        ([f"새 관점({label(fresh)})"] if fresh else [])
    return [_ANGLES[n] for n in order], "이전 시도 판정 실패 → " + (" + ".join(parts) or label(order))


def attack_context(ws: Workspace, ticket: Ticket, method: str) -> dict:
    """공격 라운드들이 공통으로 읽던 것을 한 번만 가져온다: 수정된 함수 코드, 호출부와 그 코드, 공격 이력."""
    from sr_agent.tools.code import get_callers, read_method

    callers = get_callers(ws, method)
    callers = callers if isinstance(callers, list) else []
    caller_code = {}
    for m in callers[:config.PREFETCH_MAX_CALLERS]:
        src = read_method(ws, m)
        if isinstance(src, str):
            caller_code[m] = src[:config.PREFETCH_CODE_CHARS]
    patched = read_method(ws, method)
    memory = attack_memory("search", ticket.type, {"method": method})
    return {
        "method": method,
        "patched_code": patched[:config.PREFETCH_CODE_CHARS] if isinstance(patched, str) else "",
        "callers": callers,
        "caller_code": caller_code,
        "attack_memory": memory[:config.ATTACK_MEMORY_TOP_K] if isinstance(memory, list) else [],
    }


def _run_round(client: OpenAI, ticket: Ticket, analysis: AnalysisOutput | None, attempt: FixAttempt,
               ctx: RunContext, angle: tuple[str, str, str], round_no: int, rounds: int,
               context: dict | None = None) -> tuple[AttackRound, list[VerifiedFinding]]:
    """관점 하나로 공격하고 발견을 확인한다. 응답 거부는 그대로 올린다 (사람 검토)."""
    rnd = AttackRound(angle=angle[0], label=angle[1])
    ctx = replace(ctx, log=ctx.log.tagged(angle=angle[0]))  # 동시에 도는 라운드의 로그 줄을 구분 (예산은 공유)
    user_input = attack_input(ticket, analysis, attempt.fix, attempt.diff, angle, round_no, rounds, context)
    try:
        result = run_agent(client, ATTACK, user_input, ctx)
    except LLMRefusal:
        raise
    except (AgentStopped, LLMError) as e:
        rnd.error = f"{type(e).__name__}: {e}"
        return rnd, []
    out = result.output
    rnd.summary, rnd.attempts = out.summary, out.attempts
    findings = [verify_finding(ctx.ws, f).model_copy(update={"angle": angle[0]}) for f in out.findings]
    rnd.findings, rnd.verified = len(findings), sum(f.verified for f in findings)
    ctx.log.write(step="attack", kind="round", round=round_no, rounds=rounds,
                  findings=rnd.findings, verified=rnd.verified, error=rnd.error)
    return rnd, findings


def _input_key(f: VerifiedFinding) -> str:
    try:
        return input_key(f.method, json.loads(f.input_json))
    except json.JSONDecodeError:
        return f.method + " " + f.input_json


def run_attack(client: OpenAI, ticket: Ticket, analysis: AnalysisOutput | None, attempt: FixAttempt,
               ctx: RunContext, rounds: int | None = None, previous: list[FixAttempt] = (),
               focused: bool = False, context: dict | None = None) -> AttackReport:
    """관점별 공격 라운드를 돌리고 발견을 확인·합친다. 라운드 실패(반복 상한·응답 오류)는 기록만 하고 계속한다.

    rounds: 라운드 수를 고정한다 (실행 옵션, Ablation·측정용). None이면 plan_angles로 정한다.
    previous·focused: 재시도에서 관점을 고르는 데 쓴다 (plan_angles). context: 미리 가져온 코드 (attack_context).
    """
    angles, why = plan_angles(attempt.number, previous, rounds, focused)
    planned = len(angles)
    report = AttackReport(planned_rounds=planned, escalation=why)
    seen: set[str] = set()
    for start in range(0, planned, config.ATTACK_PARALLEL):
        batch = angles[start:start + config.ATTACK_PARALLEL]
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            futures = [pool.submit(_run_round, client, ticket, analysis, attempt, ctx, angle, start + i + 1, planned,
                                   context)
                       for i, angle in enumerate(batch)]
            results = [f.result() for f in futures]  # 거부·토큰 상한 초과는 여기서 올라간다
        for rnd, findings in results:
            report.rounds.append(rnd)
            report.attempts += [f"[{rnd.label}] {a}" for a in rnd.attempts]
            for f in findings:
                if (key := _input_key(f)) not in seen:
                    seen.add(key)
                    report.findings.append(f)
        if report.verified():
            break
    report.summary = "\n".join(f"[{r.label}] {r.summary}" for r in report.rounds if r.summary)
    if report.rounds and all(r.error for r in report.rounds):
        report.error = "; ".join(f"[{r.label}] {r.error}" for r in report.rounds)
    ctx.log.write(step="attack", kind="verify", findings=len(report.findings), verified=len(report.verified()),
                  kinds=[f.kind for f in report.verified()], planned_rounds=planned,
                  rounds=[r.angle for r in report.rounds], escalation=why)
    return report


def confirmed_findings(report: AttackReport, judge: JudgeResult) -> list[VerifiedFinding]:
    """공격 이력에 남길 발견: 판정이 정답(표준화는 원본)과 다르다고 다시 확인한 입력.
    판정이 다시 비교하지 못했으면(정답 구현 없음 등) 코드로 확인된 발견."""
    check = next((c for c in judge.checks if c.name == "attack_findings_rechecked"), None)
    if check is None or "failed_keys" not in check.data:
        return report.verified()
    failed = set(check.data["failed_keys"])
    return [f for f in report.findings if _input_key(f) in failed]


def save_confirmed(ticket: Ticket, report: AttackReport, judge: JudgeResult, ctx: RunContext) -> int:
    """판정으로 확인된 공격을 공격 이력(F4-3)에 저장하고 저장 수를 돌려준다."""
    saved = 0
    for f in confirmed_findings(report, judge):
        try:
            expected = json.loads(f.expected_json)
        except json.JSONDecodeError:
            expected = f.expected_json
        result = attack_memory("save", ticket.type, {
            "ticket_id": ticket.ticket_id, "method": f.method, "kind": f.kind, "input": json.loads(f.input_json),
            "expected": expected, "actual": f.patched, "note": f.explanation,
        })
        saved += isinstance(result, list)
    report.saved_to_memory = saved
    ctx.log.write(step="attack", kind="memory", saved=saved, findings=len(report.findings))
    return saved
