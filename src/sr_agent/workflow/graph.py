"""고정 워크플로 그래프 (PRD 7.1, F5-1 ~ F5-5). 프레임워크 없이 직접 짠다.

분석 → 재현 확인(코드) → 수정 → 공격 → 판정(코드) ─통과→ 승인 대기
  재현 테스트가 원본에서 실패하지 않으면 분석을 한 번 더 (config.MAX_ANALYSIS_TRIES)
  판정 실패 시 실패 내용을 붙여 수정 재시도 (첫 시도 + config.MAX_PATCH_RETRIES회), 초과하면 사람 검토
노드 사이에는 구조화 결과만 넘기고, 노드가 끝날 때마다 state.json에 저장한다 (중단 후 이어서 실행).
공격 발견은 코드로 다시 확인해 기록하고 재시도 입력에 붙인다. 통과·실패는 판정 노드가 정한다.
코드 표준화·보안 유형은 분석을 건너뛰고 티켓에 적힌 대상 함수로 수정부터 한다 (PRD 12장 기본안).
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from openai import OpenAI

from sr_agent import config
from sr_agent.agents.context import BudgetExceeded, RunContext
from sr_agent.agents.loop import AgentStopped, run_agent
from sr_agent.agents.specs import ANALYSIS, FIX, analysis_input, fix_input
from sr_agent.llm import LLMError, LLMRefusal
from sr_agent.tools.code import read_method
from sr_agent.tools.execution import run_tests
from sr_agent.tools.predict import predict_fault_location
from sr_agent.tools.workspace import Workspace, compare_hashes, hash_tree, prepare_workspace
from sr_agent.workflow.attack import run_attack, save_confirmed
from sr_agent.workflow.judge import judge_ticket
from sr_agent.workflow.reference import build_reference
from sr_agent.workflow.state import FixAttempt, Ticket, TicketState

FINAL = {"approval_pending", "human_review", "approved", "rejected"}


@dataclass(frozen=True)
class RunOptions:
    """Ablation(F7-3)용 조건. 기본은 전체 기능."""

    use_predict: bool = True  # 분석 에이전트에 predict_fault_location 제공
    use_attack: bool = True  # 공격 단계 실행
    attack_rounds: int | None = None  # 공격 라운드 수 고정 (None이면 기본 3, 재시도면 5)


def _analysis_spec(options: RunOptions):
    if options.use_predict:
        return ANALYSIS
    return replace(ANALYSIS, tools=tuple(t for t in ANALYSIS.tools if t != "predict_fault_location"))


def resolve_repro_path(ws: Workspace, path: str) -> str:
    """보고한 재현 테스트 경로가 없으면 실제로 작성된 재현 테스트 중 가장 최근 파일을 쓴다."""
    if (ws.root / path).is_file():
        return path
    written = sorted(ws.repro_tests.glob("test_*.py"), key=lambda p: p.stat().st_mtime)
    return f"{config.REPRO_DIRNAME}/{written[-1].name}" if written else path


def _failed_assertions(result: dict) -> str:
    """실패한 단언의 코드 줄만 (값은 빼서 정답 구현의 출력을 알려 주지 않는다)."""
    lines = []
    for f in result["failed"]:
        stmt = next((ln.lstrip(">").strip() for ln in f["trace"].splitlines() if ln.startswith(">")), "")
        lines.append(f"{f['test'].split('::')[-1]}: {stmt}")
    return "; ".join(lines)


def verify_repro(ws: Workspace, path: str, ticket: Ticket | None = None) -> str | None:
    """재현 테스트가 원본에서 실제로 (테스트 오류가 아니라 단언 실패로) 실패하는지. 문제가 있으면 설명.

    판정 명세에 정답 구현이 있으면, 재현 테스트가 정답 구현에서는 통과하는지도 확인한다.
    재현 테스트는 수정 에이전트가 고칠 수 없는 "정답"이라, 기대값이 틀리면 올바른 패치도 통과하지 못한다.
    (정답 코드는 에이전트에 보여 주지 않고, 기대값이 틀렸다는 사실만 알린다)
    """
    if not path.startswith(config.REPRO_DIRNAME + "/"):
        return f"재현 테스트 경로는 {config.REPRO_DIRNAME}/ 아래여야 함: {path}"
    if not (ws.root / path).is_file():
        return ("재현 테스트를 작성하지 않음: write_repro_test를 호출하지 않고 결과를 정리했다. 원인 함수를 정했으면 "
                "탐색을 멈추고 바로 write_repro_test로 작성한 뒤 run_tests로 원본 실패를 확인한다.")
    result = run_tests(ws, "original", [path], timeout=config.JUDGE_TEST_TIMEOUT_S)
    if "error" in result:
        return f"재현 테스트 실행 오류: {result['error']}"
    if not result["failed"]:
        return f"재현 테스트가 원본에서 통과함 (결함을 드러내지 못함): {path}"
    if any("::" not in f["test"] for f in result["failed"]):
        return "재현 테스트를 불러오지 못함 (import·문법 오류): " + result["failed"][0]["trace"][-500:]
    if ticket is not None and ticket.judge is not None and ticket.judge.reference is not None:
        build_reference(ws, ticket.judge)
        ref = run_tests(ws, "reference", [path], timeout=config.JUDGE_TEST_TIMEOUT_S)
        if "error" in ref:
            return f"재현 테스트 실행 오류 (업무 규칙대로 고친 코드): {ref['error']}"
        if ref["failed"]:
            return (f"재현 테스트의 기대값이 업무 규칙과 다름: 업무 규칙대로 올바르게 고친 코드에서도 실패하는 단언 "
                    f"[{_failed_assertions(ref)}]. 이 단언을 빼거나, search_rules로 찾은 규칙으로 기대값을 다시 "
                    "계산한다. 티켓 증상과 직접 관계없는 추가 검증은 넣지 않는다.")
    return None


def _work_diff(ws: Workspace) -> str:
    import difflib

    changed = compare_hashes(hash_tree(ws.original), hash_tree(ws.work))["modified"]
    parts = []
    for rel in changed:
        a = (ws.original / rel).read_text(encoding="utf-8").splitlines(keepends=True)
        b = (ws.work / rel).read_text(encoding="utf-8").splitlines(keepends=True)
        parts.append("".join(difflib.unified_diff(a, b, fromfile=f"a/{rel}", tofile=f"b/{rel}", n=2)))
    return "".join(parts)


def _analysis_node(client: OpenAI, state: TicketState, ctx: RunContext, progress: Callable[[str], None],
                   options: RunOptions) -> None:
    feedback = None
    if not state.prediction:  # 화면·평가용 (분석이 실패해도 모델 정확도를 집계할 수 있게 먼저 저장)
        prediction = predict_fault_location(state.ticket.text, 3)
        state.prediction = prediction if isinstance(prediction, list) else []
    while state.analysis_tries < config.MAX_ANALYSIS_TRIES:
        state.analysis_tries += 1
        progress(f"분석 에이전트 실행 ({state.analysis_tries}/{config.MAX_ANALYSIS_TRIES})")
        result = run_agent(client, _analysis_spec(options), analysis_input(state.ticket, feedback), ctx)
        state.analysis = result.output
        state.analysis.repro_test_path = resolve_repro_path(ctx.ws, state.analysis.repro_test_path)
        problem = verify_repro(ctx.ws, state.analysis.repro_test_path, state.ticket)
        if problem is None and isinstance(read_method(ctx.ws, state.analysis.final_method), dict):
            problem = f"final_method가 대상 프로젝트에 없음: {state.analysis.final_method}"
        ctx.log.write(step="analysis", kind="verify", ok=problem is None,
                      detail=problem or "재현 테스트 원본 실패 확인")
        if problem is None:
            state.repro_verified = True
            return
        progress(f"재현 확인 실패: {problem}")
        feedback = problem
    raise _HumanReview(f"분석 실패: {feedback}")


def _fix_and_judge(client: OpenAI, state: TicketState, ctx: RunContext, progress: Callable[[str], None],
                   options: RunOptions) -> None:
    analysis = state.analysis
    method = analysis.final_method if analysis else state.ticket.method
    repro = [analysis.repro_test_path] if analysis else []
    while not state.passed and len(state.attempts) < 1 + config.MAX_PATCH_RETRIES:
        number = len(state.attempts) + 1
        progress(f"수정 에이전트 실행 (시도 {number}/{1 + config.MAX_PATCH_RETRIES})")
        attempt = FixAttempt(number=number)
        try:
            attempt.fix = run_agent(client, FIX, fix_input(state.ticket, analysis, state.attempts), ctx).output
        except AgentStopped as e:
            attempt.error = str(e)
        attempt.diff = _work_diff(ctx.ws)
        if options.use_attack:
            progress("공격 에이전트 실행 (관점별 라운드)")
            attempt.attack = run_attack(client, state.ticket, analysis, attempt, ctx, options.attack_rounds)
            report = attempt.attack
            more = f", 늘린 이유: {report.escalation}" if report.escalation else ""
            progress(f"공격 라운드 {len(report.rounds)}/{report.planned_rounds}회{more} → "
                     f"발견 {len(report.verified())}건 (확인됨) → 판정 (코드)")
        else:
            progress("공격 단계 생략 (Ablation) → 판정 (코드)")
        attempt.judge = judge_ticket(ctx.ws, state.ticket, method, repro, attempt.attack)
        ctx.log.write(step="judge", kind="judge", attempt=number, passed=attempt.judge.passed,
                      failed=[c.name for c in attempt.judge.failures()])
        if attempt.attack:
            save_confirmed(state.ticket, attempt.attack, attempt.judge, ctx)  # 판정이 확인한 공격만 이력에
        state.attempts.append(attempt)
        state.save(ctx.ws.state_path)
    if not state.passed:
        raise _HumanReview(f"판정 실패 {len(state.attempts)}회 (재시도 상한 초과)")


class _HumanReview(Exception):
    """워크플로를 멈추고 사람 검토로 넘긴다."""


def run_ticket(
    ticket: Ticket,
    client: OpenAI,
    source: Path | None = None,
    runs_dir: Path | None = None,
    restart: bool = False,
    progress: Callable[[str], None] = lambda msg: None,
    options: RunOptions | None = None,
) -> TicketState:
    """티켓을 끝까지 처리한다. state.json이 있으면 끝난 단계는 건너뛰고 이어서 실행한다."""
    if ticket.type != "defect" and not ticket.method:
        raise ValueError(f"{ticket.type} 유형 티켓에는 대상 함수(method)가 필요함: {ticket.ticket_id}")
    options = options or RunOptions()
    ws = prepare_workspace(ticket.ticket_id, source=source, runs_dir=runs_dir, overwrite=restart)
    state = None if restart else TicketState.load(ws.state_path)
    if state is not None and state.status in FINAL:
        progress(f"이미 끝난 티켓 (상태: {state.status}). 처음부터 하려면 --restart")
        return state
    state = state or TicketState(ticket=ticket)
    ctx = RunContext.new(ticket.ticket_id, ws=ws, runs_dir=runs_dir)
    ctx.budget.used = state.tokens_used
    state.run_ids.append(ctx.log.run_id)
    try:
        if ticket.type == "defect" and not state.repro_verified:
            state.status = "analyzing"
            state.save(ws.state_path)
            _analysis_node(client, state, ctx, progress, options)
            state.save(ws.state_path)
        state.status = "fixing"
        _fix_and_judge(client, state, ctx, progress, options)
        state.status = "approval_pending"
    except _HumanReview as e:
        state.status, state.human_review_reason = "human_review", str(e)
    except (LLMRefusal, BudgetExceeded, AgentStopped, LLMError) as e:
        state.status, state.human_review_reason = "human_review", f"{type(e).__name__}: {e}"
    finally:
        state.tokens_used = ctx.budget.used
        state.save(ws.state_path)
        ctx.log.write(step="workflow", kind="end", status=state.status, reason=state.human_review_reason,
                      tokens=state.tokens_used)
    return state
