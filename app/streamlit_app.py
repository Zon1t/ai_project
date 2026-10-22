"""RedFix SR 처리 화면 (PRD F6, 8장).

실행: streamlit run app/streamlit_app.py
- 재생 모드: data/replay의 사전 실행 결과를 보여 준다 (API 호출 없음, PRD 9장)
- 실시간 모드: 티켓을 실제로 처리한다 (OpenAI API 호출, 1건에 2~5분)

화면 원칙: 결론(판정·핵심 수치)을 먼저 보여 주고, 근거와 원본 로그는 펼쳐야 보이게 둔다.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st  # noqa: E402

from sr_agent import config, demo, ui  # noqa: E402
from sr_agent.jobs import JobManager  # noqa: E402
from sr_agent.rag.kb import load_rules  # noqa: E402
from sr_agent.workflow.state import Ticket, TicketState  # noqa: E402

STAGE_BADGES = {  # 진행 단계 상태 → (색, 아이콘)
    "done": ("green", ":material/check:"), "current": ("blue", ":material/radio_button_checked:"),
    "pending": ("gray", ":material/schedule:"), "skipped": ("gray", ":material/remove:"),
    "failed": ("red", ":material/close:"),
}
CHECK_ICONS = {True: ":green[:material/check_circle:]", False: ":red[:material/cancel:]",
               None: ":gray[:material/do_not_disturb_on:]"}
STATUS_COLORS = {"approval_pending": "orange", "approved": "green", "rejected": "red", "human_review": "red",
                 "new": "gray", "needs_info": "orange", "analyzing": "blue", "fixing": "blue"}
KIND_LABELS = {"under_fix": "미달", "regression": "회귀"}
EVIDENCE_KINDS = {"model": "모델 예측", "history": "장애 이력", "caller": "호출부", "coverage": "커버리지",
                  "test": "테스트", "code": "코드", "rule": "사내 규칙"}

INTAKE_HINTS = {
    "defect": "결함 수정 티켓에는 어떤 상황에서 무엇이 어떻게 잘못됐는지(증상)와 올바른 결과를 적습니다.",
    "standardize": "코드 표준화 티켓에는 바꿀 대상(예: utcnow, pytz)과 지킬 표준을 적습니다.",
    "security": "보안 진단 조치 티켓에는 진단 항목(예: SQL 문자열 결합, 개인정보 로그 출력)과 조치 요청을 적습니다.",
}
GUIDE_PATH = Path(__file__).resolve().parents[1] / "docs" / "user_guide.md"
HELP = {  # 영역 제목 옆 ? 아이콘 설명 (자세한 내용은 사용 설명서 docs/user_guide.md)
    "mode": "재생: 미리 실행해 저장한 결과(data/replay)를 API 호출 없이 다시 보여 줍니다. 진행 과정을 같은 순서로 "
            "재생하고, 승인·반려는 복사본에만 기록돼 '기록 초기화'로 되돌릴 수 있습니다.  \n"
            "실시간: 티켓을 실제로 처리합니다 (OpenAI API, 티켓당 3~7분·$0.4~1). 새 티켓 등록도 여기서 합니다. "
            "중간에 멈춰도 끝난 단계부터 이어서 실행합니다.",
    "verdict": "판정은 LLM이 아니라 코드가 유형별 통과 조건을 검사해 정합니다. 실패한 조건은 재시도하는 수정 "
               "에이전트에 전달됩니다. 회색(미평가)은 비교할 기준이 없어 자동으로 확인하지 못한 조건입니다.",
    "prediction": "결함 위치 예측 모델(XGBoost Ranker)의 Top-3입니다. 티켓-코드 유사도, 스택트레이스, "
                  "함수 길이·복잡도로 점수를 매기며 참고 자료일 뿐입니다. 최종 위치는 오른쪽 에이전트 판단을 따릅니다.",
    "attack": "공격 에이전트가 관점(경계값·빈 값·호출 경로·조합·성질)별로 입력을 만들어 고친 코드를 공격합니다. "
              "미달 = 덜 고침, 회귀 = 원래 맞던 동작을 깨뜨림. 맞고 틀림은 판정이 코드로 다시 실행해 정합니다.",
    "attack_summary": "공격 에이전트는 문제가 될 만한 입력을 '제기'만 합니다. 판정이 그 입력을 직접 다시 실행해 "
                      "정답 구현(표준화는 원본)과 다를 때만 실제 문제로 셉니다.",
    "approval": "판정을 통과한 티켓만 승인할 수 있습니다. 승인하면 마지막 패치가 runs/<티켓ID>/approved.diff로 "
                "확정됩니다. 실제 저장소 반영은 이 diff로 직접 합니다.",
}

st.set_page_config(page_title="RedFix · SR 처리", page_icon=":material/build_circle:", layout="wide")


@st.cache_resource
def _load_env() -> None:
    """.env를 환경 변수로 (API 키·실시간 모드 비밀번호). 서버 프로세스에서 한 번."""
    from dotenv import load_dotenv

    load_dotenv(config.PROJECT_ROOT / ".env")


_load_env()


@st.dialog("사용 설명서", width="large", icon=":material/menu_book:")
def guide_dialog() -> None:
    text = GUIDE_PATH.read_text(encoding="utf-8") if GUIDE_PATH.is_file() else "사용 설명서 파일이 없습니다."
    st.markdown(text.split("\n", 1)[1] if text.startswith("# ") else text)  # 맨 위 제목은 대화 상자 제목과 겹침


@st.cache_resource
def _client():
    from dotenv import load_dotenv

    from sr_agent import llm

    load_dotenv(config.PROJECT_ROOT / ".env")
    return llm.get_client()


@st.cache_data
def _rules() -> dict:
    return {r["rule_id"]: r for r in load_rules()}


def _badge(text: str, color: str, icon: str = "") -> str:
    return f":{color}-badge[{icon + ' ' if icon else ''}{text}]"


# ---------------- 사이드바: 모드, 티켓 목록·등록 ----------------

def _status_of(ticket_id: str, replay: bool) -> str:
    path = ui.replay_working_path(ticket_id) if replay else ui.live_state_path(ticket_id)
    if replay and not path.is_file():
        path = ui.replay_source(ticket_id) / config.STATE_FILENAME
    state = TicketState.load(path)
    return ui.STATUS_LABELS[state.status] if state else "미실행"


def sidebar() -> tuple[bool, Ticket | None]:
    with st.sidebar:
        st.title("RedFix", icon=":material/build_circle:")
        st.caption("변경 요청(SR) 처리 에이전트")
        mode = st.segmented_control("모드", ["재생", "실시간"], default="재생", required=True, key="mode",
                                    help=HELP["mode"])
        replay = mode == "재생"
        if not replay and not live_unlocked():
            return replay, None
        tickets = ui.replay_ticket_list() if replay else ui.all_tickets()
        if not tickets:
            st.info("재생할 결과가 없습니다." if replay else "티켓이 없습니다.")
            return replay, None
        by_id = {t.ticket_id: t for t in tickets}

        st.subheader("티켓", divider="gray")
        ids = list(by_id)
        if (pending := st.session_state.pop("_select_ticket", None)) in by_id:  # 방금 불러온 시연 케이스 선택
            st.session_state["ticket"] = pending
        captions = [" · ".join(x for x in (by_id[i].scenario, _status_of(i, replay)) if x) for i in ids]
        box = st.container(height=config.SIDEBAR_LIST_HEIGHT if len(ids) > config.SIDEBAR_LIST_ROWS else "content",
                           border=False)
        selected = box.radio("티켓", ids, key="ticket", label_visibility="collapsed", captions=captions,
                             format_func=lambda tid: f"{tid}  {ui.TYPE_LABELS[by_id[tid].type]}")

        if not replay:
            demo_loader()
            cleanup_panel()
            with st.expander("새 티켓 등록", icon=":material/add:"):
                with st.form("register", clear_on_submit=True, border=False):
                    kind = st.selectbox("유형", list(ui.TYPE_LABELS), format_func=ui.TYPE_LABELS.get)
                    title = st.text_input("제목")
                    body = st.text_area("본문 (증상)")
                    method = st.text_input("대상 함수", placeholder="customer_dao.find_by_email",
                                           help="코드 표준화·보안 유형만 입력합니다 (모듈.함수).")
                    if st.form_submit_button("등록", type="primary", width="stretch"):
                        try:
                            new = ui.register_ticket(kind, title, body, method)
                            st.toast(f"{new.ticket_id} 등록됨", icon=":material/check:")
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))
        st.caption("재생 모드는 사전 실행 결과를 API 호출 없이 보여 줍니다." if replay
                   else "실시간 모드는 OpenAI API를 호출합니다 (티켓당 2~7분).")
    return replay, by_id[selected]


def live_unlocked() -> bool:
    """실시간 모드 비밀번호 (.env의 config.LIVE_PASSWORD_ENV). 설정이 없으면 늘 열림. 맞히면 이 접속(세션) 동안 유지."""
    import hmac
    import os

    expected = os.environ.get(config.LIVE_PASSWORD_ENV, "")
    if not expected or st.session_state.get("_live_ok"):
        return True
    with st.form("live_password", border=False):
        typed = st.text_input("실시간 모드 비밀번호", type="password",
                              help="실시간 모드는 OpenAI API를 호출합니다. 관리자에게 받은 비밀번호를 입력하세요.")
        if st.form_submit_button("들어가기", type="primary", width="stretch"):
            if hmac.compare_digest(typed.encode(), expected.encode()):
                st.session_state["_live_ok"] = True
                st.rerun()
            st.error("비밀번호가 다릅니다.")
    st.caption("재생 모드는 비밀번호 없이 볼 수 있습니다.")
    return False


def demo_loader() -> None:
    """시연 케이스 불러오기: 홀드아웃 결함을 심은 작업본(A~C) 또는 정답 없는 새 티켓(D)을 등록한다."""
    cases = {c["case_id"]: c for c in demo.demo_cases()}
    if not cases:
        return
    with st.expander("시연 케이스 불러오기", icon=":material/science:"):
        cid = st.selectbox("케이스", list(cases), format_func=lambda c: f"{c} · {cases[c]['label']}",
                           label_visibility="collapsed")
        st.caption(cases[cid]["goal"])
        if st.button("불러오기", icon=":material/download:", width="stretch"):
            with st.spinner("결함을 심은 작업본을 준비하는 중 (약 10초)"):
                ticket = ui.register_demo(demo.prepare_demo(cid))
            st.session_state["_select_ticket"] = ticket.ticket_id
            st.rerun()


def demo_panel(ticket: Ticket, expanded: bool = True) -> None:
    """시연 케이스에서 발표자가 확인할 것."""
    try:
        case = demo.find_case(ticket.demo_case)
    except ValueError:
        return
    with st.expander(f"시연 케이스 {case['case_id']} · {case['label']}", expanded=expanded,
                     icon=":material/science:"):
        st.caption(case["goal"])
        st.markdown("\n".join(f"{i}. {c}" for i, c in enumerate(case["check"], 1)))


# ---------------- 상단: 티켓, 진행 단계, 실행 ----------------

def header(ticket: Ticket, state: TicketState | None) -> None:
    row = st.container(horizontal=True, vertical_alignment="center")
    with row:
        st.header(f"{ticket.ticket_id} {ticket.title}")
        if st.button("사용 설명서", icon=":material/help:", type="tertiary", help="RedFix 사용 설명서 열기"):
            guide_dialog()
    status = state.status if state else "new"
    badges = [_badge(ui.TYPE_LABELS[ticket.type], "blue"),
              _badge(ui.STATUS_LABELS[status] if state else "미실행", STATUS_COLORS.get(status, "gray"))]
    if ticket.scenario:
        badges.insert(1, _badge(f"시연 {ticket.scenario}", "violet"))
    if ticket.method:
        badges.append(_badge(ticket.method, "gray", ":material/function:"))
    st.markdown(" ".join(badges))
    with st.container(border=True):
        st.markdown(f":gray[접수 내용]  \n{ticket.body}")


def _stage_line(items: list[tuple[str, str]]) -> str:
    parts = []
    for name, status in items:
        color, icon = STAGE_BADGES[status]
        parts.append(_badge(name + (" 생략" if status == "skipped" else ""), color, icon))
    return " :gray[:material/chevron_right:] ".join(parts)


def _initial_stages(ticket: Ticket) -> list[tuple[str, str]]:
    return [(s, "skipped" if s == "분석" and ticket.type != "defect" else "pending") for s in ui.STAGES]


def _replay_stages(ticket: Ticket, step: str) -> list[tuple[str, str]]:
    """재생 중인 단계까지 완료로, 지금 단계는 진행 중으로 표시."""
    order = {"analysis": 0, "fix": 1, "attack": 2, "judge": 3}
    now = order.get(step, 0)
    items = []
    for i, (name, status) in enumerate(_initial_stages(ticket)):
        if status != "skipped":
            status = "done" if i < now else ("current" if i == now else "pending")
        items.append((name, status))
    return items


def replay_progress(ticket: Ticket, events: list[dict], stage_slot) -> None:
    """저장된 실행을 재생한다. 진행 중에는 지금 단계 한 줄만 보이고, 끝나면 요약을 띄운다."""
    shown = [(e, label) for e in events if (label := ui.progress_label(e))]
    bar = st.progress(0.0, text="재생 준비 중")
    for i, (event, label) in enumerate(shown, 1):
        stage_slot.markdown(_stage_line(_replay_stages(ticket, event.get("step", ""))))
        bar.progress(i / len(shown), text=f":material/autorenew: {label}")
        time.sleep(config.REPLAY_EVENT_DELAY_S)
    bar.empty()
    st.toast("재생 완료 — 처리 과정 탭에서 단계별 요약을 볼 수 있습니다.", icon=":material/check:")


def run_controls(row, replay: bool, ticket: Ticket, events: list[dict], stage_slot) -> bool:
    """진행 단계 줄 오른쪽의 실행·재생 버튼. 상태가 바뀌었으면 True."""
    row.space("stretch")
    if replay:
        play = row.button("진행 과정 재생", icon=":material/play_arrow:", type="primary")
        reset = row.button("승인·반려 기록 초기화", icon=":material/restart_alt:", type="tertiary")
        if play:
            replay_progress(ticket, events, stage_slot)
        if reset:
            ui.load_replay(ticket.ticket_id, reset=True)
            return True
        return False
    finished = _jobs().get(ticket.ticket_id)
    if finished is not None and finished.done:  # 방금 끝난 백그라운드 실행의 결과를 한 번 알린다
        if finished.error:
            st.error(finished.error, title="실행이 멈췄습니다", icon=":material/error:")
        else:
            st.toast(f"{ticket.ticket_id} 처리 끝: {ui.STATUS_LABELS.get(finished.result, finished.result)}",
                     icon=":material/check:")
        _jobs().forget(ticket.ticket_id)
    run = row.button("실행", icon=":material/play_arrow:", type="primary")
    restart = row.toggle("처음부터 다시", help="저장된 상태와 작업본을 지우고 처음부터 실행합니다.")
    has_results = ui.live_state_path(ticket.ticket_id).is_file() or ui.is_registered(ticket.ticket_id)
    if row.button("삭제", icon=":material/delete:", disabled=not has_results, key="delete_results",
                  help="이 티켓의 실행 결과(작업본·상태·로그)를 지웁니다. 등록한 티켓은 목록에서도 뺄 수 있습니다."
                  if has_results else "지울 실행 결과가 없습니다."):
        st.session_state[_DELETE_KEY] = ticket.ticket_id
    if st.session_state.get(_DELETE_KEY) == ticket.ticket_id:  # 확인 창은 닫을 때까지 열어 둔다
        delete_dialog(ticket)
    elif _DELETE_KEY in st.session_state:  # 다른 티켓으로 옮기면 열려 있던 확인 창은 닫는다
        _close_delete()
    if run:
        try:
            start_job(ticket, restart)
        except ValueError as e:  # 다른 사람이 막 실행을 시작한 경우
            st.error(str(e))
            return False
        return True
    return False


@st.cache_resource
def _jobs() -> JobManager:
    """서버 프로세스에 하나 (접속한 모든 사람이 공유). 같은 티켓은 동시에 하나만 실행한다."""
    return JobManager()


def start_job(ticket: Ticket, restart: bool) -> None:
    """실행을 백그라운드 스레드에서 시작한다. 화면을 조작하거나 새로고침해도 멈추지 않는다."""
    client = _client()

    def work(progress) -> str:
        from sr_agent.workflow.graph import run_ticket

        t = ticket
        if t.demo_case:  # 시연 케이스는 결함을 심은 작업본에서 돈다 (처음부터 다시면 결함 버전부터 새로)
            t = ui.register_demo(demo.prepare_demo(t.demo_case, reset=restart))
        return run_ticket(t, client, restart=restart and not t.demo_case, progress=progress).status

    _jobs().start(ticket.ticket_id, work)


@st.fragment(run_every=config.UI_POLL_S)
def running_panel(ticket: Ticket) -> None:
    """실행 중인 티켓: 진행 단계와 지금 하는 일. 끝나면 화면 전체를 다시 그린다."""
    job = _jobs().get(ticket.ticket_id)
    if job is None or job.done:
        st.rerun()
    state = TicketState.load(ui.live_state_path(ticket.ticket_id))
    st.markdown(_stage_line(ui.stage_status(state) if state else _initial_stages(ticket)))
    with st.container(border=True):
        st.markdown(f":blue[:material/autorenew:] **:shimmer[{job.last_message}]**")
        st.caption("서버에서 처리 중입니다. 다른 티켓을 보거나 새로고침해도 멈추지 않습니다. "
                   "끝나면 이 화면이 결과로 바뀝니다.")
        with st.expander("진행 기록", icon=":material/list:"):
            st.code("\n".join(m for _, m in job.messages) or "시작하는 중", language=None)


_DELETE_KEY = "_delete_pending"  # 삭제 확인 창을 띄울 티켓 ID


def _close_delete() -> None:
    st.session_state.pop(_DELETE_KEY, None)


@st.dialog("실행 결과 삭제", icon=":material/delete:", on_dismiss=_close_delete)
def delete_dialog(ticket: Ticket) -> None:
    tid = ticket.ticket_id
    registered = ui.is_registered(tid)
    st.markdown(f"**{tid}**의 작업본·상태·실행 로그(`runs/{tid}*`)를 지웁니다. 되돌릴 수 없습니다.  \n"
                ":gray[재생 모드의 저장 데이터는 지우지 않습니다.]")
    remove = st.checkbox("티켓도 목록에서 삭제", value=registered, disabled=not registered,
                         help="화면에서 등록했거나 불러온 티켓만 뺄 수 있습니다 (S1~S3는 고정).")
    row = st.container(horizontal=True, horizontal_alignment="right")
    if row.button("취소", key="delete_cancel"):
        _close_delete()
        st.rerun()
    if row.button("삭제", type="primary", icon=":material/delete:", key="delete_confirm"):
        if _jobs().running(tid):
            st.error("실행 중인 티켓은 지울 수 없습니다.")
            return
        _close_delete()
        result = ui.delete_results(tid, remove_ticket=remove)
        _jobs().forget(tid)
        if result["ticket_removed"]:
            st.session_state.pop("ticket", None)
        st.toast(f"{tid} 삭제: 로그 {result['logs']}개" + (", 목록에서 뺌" if result["ticket_removed"] else ""),
                 icon=":material/delete:")
        st.rerun()


def cleanup_panel() -> None:
    """사이드바: 여러 티켓의 실행 결과를 한 번에 지운다."""
    # 실행 결과가 있는 티켓 + 실행하지 않은 등록 티켓
    candidates = set(ui.tickets_with_results()) | {t.ticket_id for t in ui.registered_tickets()}
    done = sorted(t for t in candidates if not _jobs().running(t))
    if not done:
        return
    with st.expander("결과 정리", icon=":material/cleaning_services:"):
        picked = st.multiselect("지울 티켓", done, placeholder="티켓 선택")
        remove = st.checkbox("등록한 티켓은 목록에서도 삭제", value=True)
        if st.button("선택한 결과 삭제", icon=":material/delete:", disabled=not picked, width="stretch"):
            for tid in picked:
                ui.delete_results(tid, remove_ticket=remove)
                _jobs().forget(tid)
            if st.session_state.get("ticket") in picked:
                st.session_state.pop("ticket", None)
            st.toast(f"{len(picked)}개 티켓의 결과를 지웠습니다", icon=":material/delete:")
            st.rerun()


# ---------------- 결론: 판정과 핵심 수치 ----------------

def intake_notice(state: TicketState) -> None:
    """접수 검사에서 돌려보낸 티켓: 무엇이 부족한지."""
    intake = state.intake
    lines = [f"- {p}" for p in intake.problems]
    if intake.output and intake.output.missing:
        lines.append("\n**보충할 정보**")
        lines += [f"- {m}" for m in intake.output.missing[:3]]
    if intake.output:
        lines.append(f"\n:gray[{intake.output.reason}]")
    st.error("\n".join(lines), title="보완 요청 — 수정 요청으로 받아들이지 않았습니다",
             icon=":material/assignment_return:")
    st.caption(INTAKE_HINTS[state.ticket.type] + " 내용을 보충해 새 티켓으로 등록하세요.")


def verdict_head(state: TicketState) -> bool:
    """판정 제목과 한 줄 요약. 보완 요청·사람 검토 사유도 여기. 판정이 있으면 True."""
    last = state.attempts[-1] if state.attempts else None
    if state.status == "needs_info" and state.intake:
        intake_notice(state)
        return False
    if state.human_review_reason:
        st.warning(state.human_review_reason, title="사람 검토 필요", icon=":material/person_alert:")
    if not (last and last.judge):
        return False
    ok = last.judge.passed
    pending = [c for c in last.judge.checks if c.passed is None]
    title = "실패" if not ok else (f"조건부 통과 (미평가 {len(pending)}개)" if pending else "통과")
    icon = ":material/gpp_bad:" if not ok else (":material/gpp_maybe:" if pending else ":material/verified:")
    st.subheader(f"판정 {title} · 시도 {last.number}회째", icon=icon, help=HELP["verdict"])
    k = ui.kpis(state)
    st.markdown(f":gray[재시도 {k['retries']}회 · {ui.attack_summary(state)}]", help=HELP["attack_summary"])
    return True


def tab_overview(state: TicketState, state_path: Path, replay: bool) -> bool:
    """요약 탭: 판정, 항목별 한 줄 요약, 실패·미평가 조건, 담당자 결정. 결정했으면 True."""
    judged = verdict_head(state)
    if state.status == "needs_info":  # 보완 요청: 고친 것이 없으니 결정할 것도 없다
        return False
    rows = ui.overview_rows(state)
    if rows:
        st.table(rows, hide_index=True)
    if judged:
        checks = state.attempts[-1].judge.checks
        for c in (c for c in checks if c.passed is False):
            st.error(f"{c.detail}", title=f"실패: {ui.CHECK_LABELS.get(c.name, c.name)}", icon=":material/cancel:")
        pending = [ui.CHECK_LABELS.get(c.name, c.name) for c in checks if c.passed is None]
        if pending:
            st.info(f"{', '.join(pending)} — 비교할 기준이 없어 확인하지 못했습니다. 승인 전에 패치 diff와 공격 로그를 "
                    "직접 확인하세요.", icon=":material/visibility:")
    changed = approval(state, state_path)
    if state.ticket.demo_case:
        answer_section(state, state_path, replay)
        demo_panel(state.ticket, expanded=False)
    return changed


def tab_checks(state: TicketState) -> None:
    """판정 조건 탭: 묶음별로 무엇을 확인했고 결과가 어땠는지."""
    last = state.attempts[-1] if state.attempts else None
    if not (last and last.judge):
        st.caption("판정 기록이 없습니다.")
        return
    st.caption("코드가 검사한 통과 조건입니다. 하나라도 실패하면 수정을 다시 시킵니다. 회색은 비교할 기준이 없어 "
               "확인하지 못한 조건입니다.")
    for group, items in ui.grouped_checks(last.judge.checks):
        st.markdown(f"**{group}**")
        for c in items:
            st.markdown(f"{CHECK_ICONS[c.passed]} {ui.CHECK_LABELS.get(c.name, c.name)}  \n"
                        f":gray[{ui.CHECK_HELP.get(c.name, '')} → {c.detail}]")


def tab_process(events: list[dict], summary: dict) -> None:
    """처리 과정 탭: 단계별 한 줄 요약. 원본 로그는 토글로만."""
    segs = summary["segments"]
    if not segs:
        st.caption("실행 기록이 없습니다.")
        return
    turns = sum(s["turns"] for s in segs)
    tools = sum(s["tools"] for s in segs)
    st.caption(f"LLM 호출 {turns}회 · 도구 호출 {tools}회")
    st.dataframe(
        [{"단계": s["stage"], "시도": s["attempt"],
          "LLM 턴": s["turns"], "도구 호출": s["tools"], "결과": s["result"]} for s in segs],
        hide_index=True, alt="단계별 처리 요약",
        column_config={"결과": st.column_config.TextColumn(width="large")},
    )
    if st.toggle("원본 실행 로그 보기", key="raw_log"):
        lines = [m for e in events if (m := ui.event_message(e))]
        st.code("\n".join(lines), language=None, height=320)


# ---------------- 탭 ----------------

def _current_code(state: TicketState) -> None:
    code = ui.current_code(state)
    if code:
        with st.expander("이 함수의 현재 코드 (수정 전)", icon=":material/code:"):
            st.code(code, language="python", line_numbers=True)


def tab_location(state: TicketState) -> None:
    if state.ticket.type != "defect":
        st.caption(f"분석 단계 생략: 진단 결과가 대상 함수 `{state.ticket.method}`를 알려 줍니다.")
        _current_code(state)
        return
    a = state.analysis
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("##### 모델 예측 (참고 자료)", help=HELP["prediction"])
        signal = state.prediction_signal or {}
        if signal.get("low"):
            st.warning("  \n".join(signal["reasons"]), title="예측 신호 낮음: 학습한 티켓 분포 밖",
                       icon=":material/report:")
        if not state.prediction:
            st.caption("예측 결과가 없습니다.")
        else:
            final = a.final_method if a else None
            st.dataframe(
                [{"순위": i, "함수": p["method"],
                  "주요 근거": (p.get("reasons") or [{}])[0].get("label", "-"),
                  "최종 선택": p["method"] == final} for i, p in enumerate(state.prediction, 1)],
                hide_index=True, alt="결함 위치 예측 Top-3",
            )
            with st.expander("SHAP 근거 상세", icon=":material/insights:"):
                st.caption("점수는 확률이 아니라 후보끼리 순서를 정하는 상대값입니다 (음수일 수 있음).")
                for i, p in enumerate(state.prediction, 1):
                    st.markdown(f"**{i}. `{p['method']}`** · 점수 {p['score']:.3f}")
                    st.dataframe([{"근거": r["label"], "값": r["value"], "기여도": round(r["contribution"], 3)}
                                  for r in p.get("reasons", [])], hide_index=True, alt=f"{p['method']} SHAP 근거")
    with right:
        st.markdown("##### 에이전트 최종 판단")
        if a is None:
            st.caption("분석 결과가 없습니다.")
            return
        rank = f"모델 {a.model_rank}위" if a.model_rank else "모델 Top-k 밖"
        if a.confident:
            st.success(f"`{a.final_method}` · {rank}", title="결함 위치", icon=":material/my_location:")
        else:
            st.error(a.blocker or "이유 없음", title="확신 불가 — 수정하지 않음", icon=":material/help:")
        if a.ticket_symptom:
            st.markdown(f"**티켓 증상** “{a.ticket_symptom}”  \n:gray[재현 테스트 확인: {a.symptom_check}]")
        st.markdown(a.reason)
        if a.disagreement:
            with st.expander("모델 1위를 고르지 않은 이유", icon=":material/compare_arrows:"):
                st.markdown(a.disagreement)
        verified = _badge("원본 실패 확인", "green", ":material/check:") if state.repro_verified \
            else _badge("원본 실패 미확인", "red", ":material/close:")
        st.markdown(f"재현 테스트 `{a.repro_test_path}` {verified}")
        _current_code(state)


def tab_evidence(state: TicketState) -> None:
    rules = _rules()
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("##### 인용한 사내 규칙")
        cited = ui.cited_rule_ids(state)
        if not cited:
            st.caption("인용한 규칙이 없습니다.")
        for rid in cited:
            r = rules.get(rid)
            if r is None:
                st.caption(f"{rid} (규칙 KB에 없음)")
                continue
            with st.expander(f"{rid} · {r['title']}", icon=":material/gavel:"):
                st.markdown(r["description"])
                st.code(r["good_example"], language="python")
    with right:
        st.markdown("##### 인용한 장애·조치 이력")
        details = ui.history_details(ui.cited_history_ids(state))
        if not details:
            st.caption("인용한 이력이 없거나 RAG 색인이 없습니다.")
        for h in details:
            with st.expander(f"{h['history_id']} · {h['method']}", icon=":material/history:"):
                st.markdown(h["ticket"])
                st.markdown(f"**원인** {h['root_cause']}  \n**조치** {h['fix_summary']}")
    if state.analysis and state.analysis.evidence:
        st.markdown("##### 분석 근거")
        st.table([{"종류": EVIDENCE_KINDS.get(e.kind, e.kind), "대상": e.ref, "내용": e.note}
                  for e in state.analysis.evidence], hide_index=True)


def _pick_attempt(state: TicketState, key: str):
    """시도가 여러 번이면 고를 수 있게 하고, 기본은 마지막 시도."""
    attempts = state.attempts
    if len(attempts) == 1:
        return attempts[0]
    numbers = [a.number for a in attempts]
    marks = {a.number: (" 통과" if a.judge and a.judge.passed else " 실패" if a.judge else "") for a in attempts}
    picked = st.segmented_control("시도", numbers, default=numbers[-1], required=True, key=key,
                                  format_func=lambda n: f"시도 {n}{marks[n]}", label_visibility="collapsed")
    return next(a for a in attempts if a.number == picked)


def tab_diff(state: TicketState) -> None:
    if not state.attempts:
        st.caption("수정 시도가 없습니다.")
        return
    att = _pick_attempt(state, f"diff-{state.ticket.ticket_id}")
    if att.fix:
        ids = [_badge(r, "blue", ":material/gavel:") for r in att.fix.rule_ids]
        ids += [_badge(h, "violet", ":material/history:") for h in att.fix.history_ids]
        st.markdown(" ".join(ids) or ":gray[인용 없음]")
        st.markdown(att.fix.patch_summary)
    if att.error:
        st.error(att.error)
    code_view(state, att)


def code_view(state: TicketState, att) -> None:
    """대상 함수의 수정 전/후 전체 코드. 나란히 보기와, 바뀐 줄을 색으로 표시한 한 화면 보기."""
    v = ui.code_versions(state, att)
    if v is None:  # 원본을 다시 만들 수 없으면 diff만
        st.code(att.diff or "(변경 없음)", language="diff", line_numbers=True)
        return
    changed = ui.changed_line_numbers(v["before"], v["after"])
    row = st.container(horizontal=True, vertical_alignment="center")
    view = row.segmented_control("보기", ["나란히", "바뀐 줄 표시"], default="나란히", required=True,
                                 key=f"view-{state.ticket.ticket_id}-{att.number}", label_visibility="collapsed")
    row.caption(f"`{v['method']}` · 바뀐 줄 {len(changed)}개 (수정 후 기준 {_ranges(changed)})")
    if view == "나란히":
        left, right = st.columns(2)
        with left:
            st.markdown("**수정 전**")
            st.code(v["before"], language="python", line_numbers=True, wrap_lines=True)
        with right:
            st.markdown("**수정 후**")
            st.code(v["after"], language="python", line_numbers=True, wrap_lines=True)
    else:
        import difflib

        lines = difflib.unified_diff(v["before"].splitlines(), v["after"].splitlines(), lineterm="",
                                     n=max(len(v["before"].splitlines()), len(v["after"].splitlines())))
        body = [ln for ln in lines if not ln.startswith(("---", "+++", "@@"))]
        st.code("\n".join(body), language="diff")
        st.caption("초록(+) 줄이 새로 들어간 코드, 빨강(-) 줄이 지운 코드입니다.")
    imports = v["imports"]
    if imports["added"] or imports["removed"]:
        parts = [f":green[+ `{i}`]" for i in imports["added"]] + [f":red[- `{i}`]" for i in imports["removed"]]
        st.markdown("같은 파일의 import 변경: " + " ".join(parts))
    with st.expander("파일 diff 원문", icon=":material/difference:"):
        st.code(att.diff or "(변경 없음)", language="diff", line_numbers=True)


def _ranges(numbers: list[int]) -> str:
    """[3, 4, 5, 9] → '3-5, 9'."""
    if not numbers:
        return "-"
    parts, start, prev = [], numbers[0], numbers[0]
    for n in [*numbers[1:], None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}" if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    return ", ".join(parts)


def tab_attack(state: TicketState) -> None:
    if not state.attempts:
        st.caption("공격 기록이 없습니다.")
        return
    att = _pick_attempt(state, f"attack-{state.ticket.ticket_id}")
    rep = att.attack
    if rep is None:
        st.caption("이 시도에는 공격 단계가 없습니다.")
        return
    if rep.error:
        st.error(rep.error)
    angles = " · ".join(r.label for r in rep.rounds)
    st.markdown("##### 공격 요약", help=HELP["attack"])
    lead = ui.attack_summary(state) + "  \n" if att is state.attempts[-1] else ""
    parts = ([f"라운드 {len(rep.rounds)}/{rep.planned_rounds}회 ({angles})"] if rep.rounds else []) + \
        [f"공격 이력 저장 {rep.saved_to_memory}건"] + \
        ([f"라운드를 늘린 이유: {rep.escalation}"] if rep.escalation else [])
    st.markdown(lead + ":gray[" + " · ".join(parts) + "]")

    if rep.findings:
        st.markdown("##### 공격이 제기한 문제")
        st.dataframe(
            [{"판정 결과": ui.finding_verdict(att, f), "공격 주장": KIND_LABELS[f.kind], "입력": f.input_json,
              "수정본": ui.format_result(f.patched), "원본": ui.format_result(f.original),
              "공격이 기대한 값": f.expected_json, "근거": f.rule_id, "설명": f.explanation}
             for f in rep.findings],
            hide_index=True, alt="공격이 제기한 문제 목록",
            column_config={"설명": st.column_config.TextColumn(width="large")},
        )
        st.caption("판정 결과: 판정이 같은 입력을 다시 실행해 정답 구현(표준화는 원본)과 비교한 결과입니다. "
                   "'문제 아님'이면 공격 에이전트의 주장과 달리 정답과 같게 동작합니다.")
    else:
        st.caption("이 시도에서 공격이 찾은 입력이 없습니다.")
    if rep.rounds:
        with st.expander("라운드별 결과", icon=":material/swords:"):
            st.table([{"관점": r.label, "제기": r.findings, "원본과 다름": r.verified, "요약": r.error or r.summary}
                      for r in rep.rounds], hide_index=True)


# ---------------- 승인 ----------------

def approval(state: TicketState, state_path: Path) -> bool:
    with st.container(border=True):
        if state.decision:
            d = state.decision
            if d.status == "approved":
                st.success(f"결과물: `{d.result_path}`", title=f"승인됨 · {d.decided_at}", icon=":material/task_alt:")
            else:
                st.error(d.reason, title=f"반려됨 · {d.decided_at}", icon=":material/block:")
            return False
        allowed = ui.can_approve(state)
        row = st.container(horizontal=True, vertical_alignment="center")
        row.markdown("**담당자 결정**", help=HELP["approval"], width="content")
        if row.button("승인 후 반영", icon=":material/check:", type="primary", disabled=not allowed,
                      help=None if allowed else "판정을 통과한 승인 대기 티켓만 승인할 수 있습니다"):
            ui.decide(state, state_path, approve=True)
            return True
        with row.popover("반려", icon=":material/undo:"):
            reason = st.text_area("반려 사유", key=f"reason-{state.ticket.ticket_id}",
                                  placeholder="예: 경계값(정확히 10만 원) 처리 근거 부족")
            if st.button("반려 확정", type="primary", width="stretch"):
                try:
                    ui.decide(state, state_path, approve=False, reason=reason)
                    return True
                except ValueError as e:
                    st.error(str(e))
    return False


def main() -> None:
    replay, ticket = sidebar()
    if ticket is None:
        st.title("RedFix")
        st.info("왼쪽에서 티켓을 고르세요.")
        return
    if replay:
        state, state_path, events = ui.load_replay(ticket.ticket_id)
    else:
        state_path = ui.live_state_path(ticket.ticket_id)
        state = TicketState.load(state_path)
        events = ui.load_events(state) if state else []
    header(ticket, state)
    if not replay and _jobs().running(ticket.ticket_id):
        running_panel(ticket)
        return
    row = st.container(horizontal=True, vertical_alignment="center")
    stage_slot = row.empty()
    stage_slot.markdown(_stage_line(ui.stage_status(state) if state else _initial_stages(ticket)))
    if run_controls(row, replay, ticket, events, stage_slot):
        st.rerun()
    stage_slot.markdown(_stage_line(ui.stage_status(state) if state else _initial_stages(ticket)))
    if state is None:
        if ticket.demo_case:
            demo_panel(ticket)
        st.info("아직 실행하지 않은 티켓입니다. 실행을 누르세요.", icon=":material/info:")
        return
    summary = ui.run_summary(events)
    # 첫 탭(요약)에서 판정·항목별 한 줄·결정까지 끝내고, 자세한 내용은 필요한 탭만 연다 (위아래로 길어지지 않게)
    tabs = st.tabs([":material/summarize: 요약", ":material/my_location: 결함 위치", ":material/menu_book: 근거",
                    ":material/difference: 패치 diff", ":material/swords: 공격 로그", ":material/rule: 판정 조건",
                    ":material/timeline: 처리 과정"])
    with tabs[0]:
        if tab_overview(state, state_path, replay):
            st.rerun()
    with tabs[1]:
        tab_location(state)
    with tabs[2]:
        tab_evidence(state)
    with tabs[3]:
        tab_diff(state)
    with tabs[4]:
        tab_attack(state)
    with tabs[5]:
        tab_checks(state)
    with tabs[6]:
        tab_process(events, summary)


def answer_section(state: TicketState, state_path: Path, replay: bool) -> None:
    """시연 케이스 정답 확인: 실제 결함 함수, 숨긴 정답 테스트 결과, 담당자 판단과의 일치."""
    case = demo.find_case(state.ticket.demo_case)
    key = f"answer-{state.ticket.ticket_id}"
    with st.container(border=True):
        st.markdown("**정답 확인**", help="승인·반려로 판단한 뒤 누르세요. 결함 때문에 실패해 에이전트·판정에서 "
                    "숨겨 둔 기존 테스트를 마지막 패치에 돌려, 승인하는 것이 맞았는지 알려 줍니다.")
        if not st.session_state.get(key):
            if st.button("정답 확인", icon=":material/fact_check:"):
                if not replay or state.answer_check is None:
                    with st.spinner("숨긴 정답 테스트를 돌리는 중"):
                        state.answer_check = demo.check_answer(state)
                    state.save(state_path)
                st.session_state[key] = True
                st.rerun()
            return
        answer = state.answer_check or {}
        if answer.get("answer_method"):
            same = answer["answer_method"] == answer.get("agent_method")
            st.markdown(f"실제 결함 함수 `{answer['answer_method']}` · 에이전트 판단 `{answer.get('agent_method')}` "
                        + (_badge("일치", "green", ":material/check:") if same
                           else _badge("다름", "red", ":material/close:")))
        tests = answer.get("tests")
        if tests:
            mark = {True: ":green[:material/check_circle: 통과]", False: ":red[:material/cancel: 실패]",
                    None: ":gray[확인 불가]"}[tests["passed"]]
            st.markdown(f"숨긴 정답 테스트 (마지막 패치): {mark} — :gray[{tests['detail']}]")
        match = demo.judgement_matches(state)
        if match is True:
            st.success("담당자 판단이 정답과 맞습니다.", icon=":material/thumb_up:")
        elif match is False:
            right = "승인" if answer.get("patch_ok") else "반려"
            st.warning(f"정답은 '{right}'입니다. 아래 설명과 패치 diff를 다시 보세요.", icon=":material/info:")
        elif answer.get("patch_ok") is not None:
            st.caption("승인 또는 반려를 먼저 하면 판단이 맞았는지 함께 알려 줍니다.")
        st.caption(case["answer_note"])


main()
