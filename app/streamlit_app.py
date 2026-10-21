"""SR 처리 에이전트 화면 (PRD F6, 8장).

실행: streamlit run app/streamlit_app.py
- 재생 모드: data/replay의 사전 실행 결과를 보여 준다 (API 호출 없음, PRD 9장)
- 실시간 모드: 티켓을 실제로 처리한다 (OpenAI API 호출, 1건에 2~5분)
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st  # noqa: E402

from sr_agent import config, ui  # noqa: E402
from sr_agent.rag.kb import load_rules  # noqa: E402
from sr_agent.workflow.state import Ticket, TicketState  # noqa: E402

MARKS = {"done": "✅", "current": "🔵", "pending": "⚪", "skipped": "➖", "failed": "❌"}
CHECK_MARKS = {True: "✅", False: "❌", None: "➖"}
KIND_LABELS = {"under_fix": "미달", "regression": "회귀"}

st.set_page_config(page_title="SR 처리 에이전트", page_icon="🛠️", layout="wide")


@st.cache_resource
def _client():
    from dotenv import load_dotenv

    from sr_agent import llm

    load_dotenv(config.PROJECT_ROOT / ".env")
    return llm.get_client()


@st.cache_data
def _rules() -> dict:
    return {r["rule_id"]: r for r in load_rules()}


# ---------------- 사이드바: 모드, 티켓 목록·등록 ----------------

def _status_of(ticket_id: str, replay: bool) -> str:
    path = ui.replay_working_path(ticket_id) if replay else ui.live_state_path(ticket_id)
    if replay and not path.is_file():
        path = ui.replay_source(ticket_id) / config.STATE_FILENAME
    state = TicketState.load(path)
    return ui.STATUS_LABELS[state.status] if state else "미실행"


def sidebar() -> tuple[bool, Ticket | None]:
    st.sidebar.title("🛠️ SR 처리 에이전트")
    mode = st.sidebar.radio("모드", ["재생 (사전 실행 결과)", "실시간 실행"], key="mode",
                            help="재생: 저장된 결과를 API 호출 없이 보여 줍니다. 실시간: 실제로 처리합니다.")
    replay = mode.startswith("재생")
    tickets = ui.all_tickets()
    if replay:
        saved = set(ui.replay_tickets())
        tickets = [t for t in tickets if t.ticket_id in saved]
    if not tickets:
        st.sidebar.info("재생할 결과가 없습니다." if replay else "티켓이 없습니다.")
        return replay, None
    by_id = {t.ticket_id: t for t in tickets}

    def label(tid: str) -> str:
        t = by_id[tid]
        tag = f"{t.scenario} · " if t.scenario else ""
        return f"{tid} · {tag}{ui.TYPE_LABELS[t.type]} · {_status_of(tid, replay)}"

    st.sidebar.subheader("티켓 목록")
    selected = st.sidebar.radio("티켓", list(by_id), format_func=label, key="ticket", label_visibility="collapsed")

    if not replay:
        with st.sidebar.expander("➕ 새 티켓 등록"):
            with st.form("register", clear_on_submit=True):
                kind = st.selectbox("유형", list(ui.TYPE_LABELS), format_func=ui.TYPE_LABELS.get)
                title = st.text_input("제목")
                body = st.text_area("본문 (증상)")
                method = st.text_input("대상 함수 (코드 표준화·보안만, 예: customer_dao.find_by_email)")
                if st.form_submit_button("등록"):
                    try:
                        new = ui.register_ticket(kind, title, body, method)
                        st.success(f"{new.ticket_id} 등록됨")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
    return replay, by_id[selected]


def sidebar_kpis(state: TicketState | None) -> None:
    st.sidebar.subheader("KPI")
    if state is None:
        st.sidebar.caption("실행 결과가 없습니다.")
        return
    k = ui.kpis(state)
    c1, c2, c3 = st.sidebar.columns(3)
    c1.metric("재시도", k["retries"])
    c2.metric("미달·회귀", f"{k['attack_under_fix']}·{k['attack_regression']}",
              help=f"공격 에이전트가 찾아 코드로 확인한 수. 판정이 잡은 불일치: 미달 {k['judge_under_fix']}, "
                   f"회귀 {k['judge_regression']}")
    c3.metric("테스트 통과", "-" if k["tests_passed"] is None else f"{k['tests_passed']:,}")
    st.sidebar.caption(f"토큰 {state.tokens_used:,}")


# ---------------- 본문 ----------------

def header(ticket: Ticket) -> None:
    tag = f" · {ticket.scenario}" if ticket.scenario else ""
    st.title(f"{ticket.ticket_id} {ticket.title}")
    st.caption(f"{ui.TYPE_LABELS[ticket.type]}{tag}" + (f" · 대상 함수 `{ticket.method}`" if ticket.method else ""))
    st.markdown(f"> {ticket.body}")


def stages(state: TicketState | None, ticket: Ticket) -> None:
    items = ui.stage_status(state) if state else [
        (s, "skipped" if s == "분석" and ticket.type != "defect" else "pending") for s in ui.STAGES]
    cols = st.columns(len(items))
    for col, (name, status) in zip(cols, items, strict=True):
        col.markdown(f"### {MARKS[status]}\n**{name}**" + (" (생략)" if status == "skipped" else ""))


def run_controls(replay: bool, ticket: Ticket, events: list[dict]) -> bool:
    """실행·재생 버튼. 상태가 바뀌었으면 True."""
    if replay:
        c1, c2 = st.columns([1, 1])
        if c1.button("▶ 진행 과정 재생", type="primary", use_container_width=True):
            with st.status("저장된 실행 과정을 재생합니다", expanded=True) as status:
                for event in events:
                    if message := ui.event_message(event):
                        status.write(message)
                        time.sleep(config.REPLAY_EVENT_DELAY_S)
                status.update(label="재생 완료", state="complete")
        if c2.button("↺ 승인·반려 기록 초기화", use_container_width=True):
            ui.load_replay(ticket.ticket_id, reset=True)
            return True
        return False
    c1, c2 = st.columns([1, 1])
    restart = c2.checkbox("처음부터 다시 (저장된 상태·작업본 삭제)")
    if c1.button("▶ 실행", type="primary", use_container_width=True):
        from sr_agent.workflow.graph import run_ticket

        with st.status("티켓을 처리하고 있습니다 (2~5분)", expanded=True) as status:
            started = time.monotonic()
            try:
                state = run_ticket(ticket, _client(), restart=restart,
                                   progress=lambda m: status.write(f"[{time.monotonic() - started:5.0f}s] {m}"))
            except ValueError as e:
                status.update(label=str(e), state="error")
                return False
            ok = state.status == "approval_pending"
            status.update(label=f"완료: {ui.STATUS_LABELS[state.status]}", state="complete" if ok else "error")
        return True
    return False


def judge_summary(state: TicketState) -> None:
    last = state.attempts[-1] if state.attempts else None
    if state.human_review_reason:
        st.warning(f"사람 검토 필요: {state.human_review_reason}")
    if not (last and last.judge):
        return
    verdict = "✅ 판정 통과" if last.judge.passed else "❌ 판정 실패"
    st.subheader(f"{verdict} (시도 {last.number}회째)")
    st.table([{"결과": CHECK_MARKS[c.passed], "조건": ui.CHECK_LABELS.get(c.name, c.name), "내용": c.detail}
              for c in last.judge.checks])


def tab_location(state: TicketState) -> None:
    if state.ticket.type != "defect":
        st.info(f"분석 단계 생략: 진단 결과가 대상 함수 `{state.ticket.method}`를 알려 줍니다.")
        return
    left, right = st.columns(2)
    with left:
        st.markdown("#### 모델 예측 (참고 자료)")
        if not state.prediction:
            st.caption("예측 결과가 없습니다.")
        for i, p in enumerate(state.prediction, 1):
            st.markdown(f"**{i}. `{p['method']}`** (점수 {p['score']:.3f})")
            st.table([{"SHAP 근거": r["label"], "값": r["value"], "기여도": f"{r['contribution']:+.3f}"}
                      for r in p.get("reasons", [])])
    with right:
        st.markdown("#### 에이전트 최종 판단")
        a = state.analysis
        if a is None:
            st.caption("분석 결과가 없습니다.")
            return
        rank = f"모델 {a.model_rank}위" if a.model_rank else "모델 Top-k 밖"
        st.success(f"결함 위치: `{a.final_method}` ({rank})")
        st.markdown(f"**판단 이유**  \n{a.reason}")
        if a.disagreement:
            st.markdown(f"**모델 1위와 다르게 고른 이유**  \n{a.disagreement}")
        verified = "예" if state.repro_verified else "아니오"
        st.markdown(f"**재현 테스트**: `{a.repro_test_path}` (원본 실패 확인: {verified})")


def tab_evidence(state: TicketState) -> None:
    rules = _rules()
    st.markdown("#### 인용한 사내 규칙")
    for rid in ui.cited_rule_ids(state):
        r = rules.get(rid)
        if r is None:
            st.caption(f"{rid} (규칙 KB에 없음)")
            continue
        with st.expander(f"{rid} · {r['title']}"):
            st.write(r["description"])
            st.code(r["good_example"], language="python")
    st.markdown("#### 인용한 장애·조치 이력")
    details = ui.history_details(ui.cited_history_ids(state))
    if not details:
        st.caption("인용한 이력이 없거나 RAG 색인이 없습니다.")
    for h in details:
        with st.expander(f"{h['history_id']} · `{h['method']}`"):
            st.write(h["ticket"])
            st.markdown(f"**원인**: {h['root_cause']}  \n**조치**: {h['fix_summary']}")
    if state.analysis and state.analysis.evidence:
        st.markdown("#### 분석 근거")
        st.table([{"종류": e.kind, "대상": e.ref, "내용": e.note} for e in state.analysis.evidence])


def tab_diff(state: TicketState) -> None:
    if not state.attempts:
        st.caption("수정 시도가 없습니다.")
    for att in reversed(state.attempts):
        passed = att.judge.passed if att.judge else None
        with st.expander(f"시도 {att.number} {CHECK_MARKS[passed]}", expanded=att is state.attempts[-1]):
            if att.fix:
                st.markdown(att.fix.patch_summary)
                st.caption(f"인용 규칙: {', '.join(att.fix.rule_ids) or '-'} · "
                           f"인용 이력: {', '.join(att.fix.history_ids) or '-'}")
            if att.error:
                st.error(att.error)
            st.code(att.diff or "(변경 없음)", language="diff")


def tab_attack(state: TicketState) -> None:
    if not state.attempts:
        st.caption("공격 기록이 없습니다.")
    for att in reversed(state.attempts):
        with st.expander(f"시도 {att.number}", expanded=att is state.attempts[-1]):
            if att.attack:
                if att.attack.rounds:
                    more = f" · 늘린 이유: {att.attack.escalation}" if att.attack.escalation else ""
                    st.caption(f"공격 라운드 {len(att.attack.rounds)}/{att.attack.planned_rounds}회{more}")
                    st.table([{"관점": r.label, "발견": r.findings, "확인": r.verified, "요약": r.error or r.summary}
                              for r in att.attack.rounds])
                else:
                    st.markdown(att.attack.summary or "")
                if att.attack.error:
                    st.error(att.attack.error)
                for label in ("under_fix", "regression"):
                    rows = [f for f in att.attack.findings if f.kind == label]
                    st.markdown(f"**{KIND_LABELS[label]}** {len([f for f in rows if f.verified])}건 (코드 확인)")
                    if rows:
                        st.table([{"확인": "✅" if f.verified else "❔", "입력": f.input_json, "기대": f.expected_json,
                                   "수정본": str(f.patched), "원본": str(f.original), "근거": f.rule_id,
                                   "설명": f.explanation} for f in rows])
                if att.attack.attempts:
                    st.caption("시도한 공격: " + " / ".join(att.attack.attempts))
            if att.judge:
                judged = [c for c in att.judge.checks if c.data.get("under_fix") or c.data.get("regression")]
                for c in judged:
                    st.markdown(f"판정 `{ui.CHECK_LABELS.get(c.name, c.name)}`: 미달 {c.data.get('under_fix', 0)}, "
                                f"회귀 {c.data.get('regression', 0)}")


def approval(state: TicketState, state_path: Path) -> bool:
    st.divider()
    if state.decision:
        d = state.decision
        if d.status == "approved":
            st.success(f"승인됨 ({d.decided_at}) — 결과물: `{d.result_path}`")
        else:
            st.error(f"반려됨 ({d.decided_at}) — 사유: {d.reason}")
        return False
    left, right = st.columns([1, 2])
    allowed = ui.can_approve(state)
    if left.button("✅ 승인 후 반영", type="primary", disabled=not allowed, use_container_width=True,
                   help=None if allowed else "판정을 통과한 승인 대기 티켓만 승인할 수 있습니다"):
        ui.decide(state, state_path, approve=True)
        return True
    reason = right.text_area("반려 사유", key=f"reason-{state.ticket.ticket_id}")
    if right.button("반려", use_container_width=True):
        try:
            ui.decide(state, state_path, approve=False, reason=reason)
            return True
        except ValueError as e:
            right.error(str(e))
    return False


def main() -> None:
    replay, ticket = sidebar()
    if ticket is None:
        st.title("SR 처리 에이전트")
        st.info("왼쪽에서 티켓을 고르세요.")
        return
    if replay:
        state, state_path, events = ui.load_replay(ticket.ticket_id)
    else:
        state_path, events = ui.live_state_path(ticket.ticket_id), []
        state = TicketState.load(state_path)
    sidebar_kpis(state)
    header(ticket)
    stages(state, ticket)
    if run_controls(replay, ticket, events):
        st.rerun()
    if state is None:
        st.info("아직 실행하지 않은 티켓입니다. ▶ 실행을 누르세요.")
        return
    judge_summary(state)
    tabs = st.tabs(["결함 위치", "근거", "패치 diff", "공격 로그"])
    with tabs[0]:
        tab_location(state)
    with tabs[1]:
        tab_evidence(state)
    with tabs[2]:
        tab_diff(state)
    with tabs[3]:
        tab_attack(state)
    if approval(state, state_path):
        st.rerun()


main()
