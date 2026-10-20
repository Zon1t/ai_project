"""티켓 하나를 CLI에서 끝까지 실행한다.

실행: python -m sr_agent.run --ticket SR-2041 [--restart]   (PYTHONPATH=src)
시연 티켓은 data/scenarios.json에 있다. 결과 상태는 runs/<ticket_id>/state.json, 실행 로그는 runs/<run_id>.jsonl.
"""

import argparse
import json
import sys
import time

from sr_agent import config
from sr_agent.workflow.state import Ticket, TicketState

SCENARIOS_PATH = config.DATA_DIR / "scenarios.json"


def load_ticket(ticket_id: str) -> Ticket:
    for item in json.loads(SCENARIOS_PATH.read_text(encoding="utf-8")):
        if item["ticket_id"] == ticket_id:
            return Ticket(**item)
    raise SystemExit(f"티켓 없음: {ticket_id} ({SCENARIOS_PATH})")


def report(state: TicketState) -> str:
    """사람이 읽을 결과 요약."""
    lines = [f"\n=== {state.ticket.ticket_id} ({state.ticket.title}) ===", f"상태: {state.status}"]
    if state.human_review_reason:
        lines.append(f"사람 검토 사유: {state.human_review_reason}")
    if a := state.analysis:
        lines += [
            "\n[분석]",
            f"결함 위치: {a.final_method} (모델 순위: {a.model_rank if a.model_rank else 'Top-k 밖'}, "
            f"모델 Top-3: {', '.join(a.model_top3)})",
            f"판단 이유: {a.reason}",
        ]
        if a.disagreement:
            lines.append(f"모델과 다른 이유: {a.disagreement}")
        lines += [f"  - [{e.kind}] {e.ref}: {e.note}" for e in a.evidence]
        lines.append(f"재현 테스트: {a.repro_test_path} (원본 실패 확인: {state.repro_verified})")
    for att in state.attempts:
        lines.append(f"\n[수정 시도 {att.number}]")
        if att.fix:
            rules = ", ".join(att.fix.rule_ids) or "-"
            history = ", ".join(att.fix.history_ids) or "-"
            lines += [f"설명: {att.fix.patch_summary}", f"인용 규칙: {rules} / 인용 이력: {history}"]
        if att.error:
            lines.append(f"오류: {att.error}")
        if att.diff:
            lines.append(att.diff.rstrip())
        if att.attack:
            label = {"under_fix": "미달", "regression": "회귀"}
            lines.append(f"공격: {att.attack.summary or att.attack.error or '-'}")
            lines += [f"  {'O' if f.verified else '?'} [{label[f.kind]}] {f.method}({f.input_json}) "
                      f"기대 {f.expected_json} / 수정본 {f.patched} — {f.check}" for f in att.attack.findings]
        if att.judge:
            lines.append(f"판정: {'통과' if att.judge.passed else '실패'}")
            mark = {True: "O", False: "X", None: "-"}
            lines += [f"  {mark[c.passed]} {c.name}: {c.detail}" for c in att.judge.checks]
    lines.append(f"\n토큰 사용: {state.tokens_used:,} / 실행 로그: runs/{state.run_ids[-1]}.jsonl"
                 if state.run_ids else "")
    return "\n".join(lines)


def main() -> None:
    from dotenv import load_dotenv

    from sr_agent import llm
    from sr_agent.workflow.graph import run_ticket

    parser = argparse.ArgumentParser(description="SR 티켓 처리 (분석 → 수정 → 판정)")
    parser.add_argument("--ticket", required=True, help="티켓 ID (data/scenarios.json)")
    parser.add_argument("--restart", action="store_true", help="저장된 상태와 작업본을 지우고 처음부터")
    args = parser.parse_args()
    load_dotenv(config.PROJECT_ROOT / ".env")
    ticket = load_ticket(args.ticket)
    started = time.monotonic()

    def progress(msg: str) -> None:
        print(f"[{time.monotonic() - started:6.1f}s] {msg}", flush=True)

    try:
        state = run_ticket(ticket, llm.get_client(), restart=args.restart, progress=progress)
    except NotImplementedError as e:
        raise SystemExit(str(e)) from None
    print(report(state))
    print(f"소요 시간: {time.monotonic() - started:.0f}초")
    sys.exit(0 if state.status == "approval_pending" else 1)


if __name__ == "__main__":
    main()
