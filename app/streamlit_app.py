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
                 "new": "gray", "analyzing": "blue", "fixing": "blue"}
KIND_LABELS = {"under_fix": "미달", "regression": "회귀"}
EVIDENCE_KINDS = {"model": "모델 예측", "history": "장애 이력", "caller": "호출부", "coverage": "커버리지",
                  "test": "테스트", "code": "코드", "rule": "사내 규칙"}

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
    "approval": "판정을 통과한 티켓만 승인할 수 있습니다. 승인하면 마지막 패치가 runs/<티켓ID>/approved.diff로 "
                "확정됩니다. 실제 저장소 반영은 이 diff로 직접 합니다.",
}

st.set_page_config(page_title="RedFix · SR 처리", page_icon=":material/build_circle:", layout="wide")


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
        selected = st.radio("티켓", ids, key="ticket", label_visibility="collapsed", captions=captions,
                            format_func=lambda tid: f"{tid}  {ui.TYPE_LABELS[by_id[tid].type]}")

        if not replay:
            demo_loader()
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


def demo_panel(ticket: Ticket) -> None:
    """시연 케이스에서 발표자가 확인할 것."""
    try:
        case = demo.find_case(ticket.demo_case)
    except ValueError:
        return
    with st.expander(f"시연 케이스 {case['case_id']} · {case['label']}", expanded=True, icon=":material/science:"):
        st.markdown(case["goal"])
        st.markdown("**확인할 것**")
        st.markdown("\n".join(f"{i}. {c}" for i, c in enumerate(case["check"], 1)))
        st.caption("판단을 마치면 맨 아래 '정답 확인'으로 맞았는지 봅니다.")


# ---------------- 상단: 티켓, 진행 단계, 실행 ----------------

def header(ticket: Ticket, state: TicketState | None) -> None:
    row = st.container(horizontal=True, vertical_alignment="center")
    with row:
        st.title(f"{ticket.ticket_id} {ticket.title}")
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
        st.caption("접수 내용")
        st.markdown(ticket.body)


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
    st.session_state["show_process"] = True
    st.toast("재생 완료 — 아래 처리 과정에서 단계별 요약을 볼 수 있습니다.", icon=":material/check:")


def run_controls(replay: bool, ticket: Ticket, events: list[dict], stage_slot) -> bool:
    """실행·재생 버튼. 상태가 바뀌었으면 True."""
    row = st.container(horizontal=True, vertical_alignment="center")
    if replay:
        play = row.button("진행 과정 재생", icon=":material/play_arrow:", type="primary")
        reset = row.button("승인·반려 기록 초기화", icon=":material/restart_alt:", type="tertiary")
        if play:
            replay_progress(ticket, events, stage_slot)
        if reset:
            ui.load_replay(ticket.ticket_id, reset=True)
            return True
        return False
    run = row.button("실행", icon=":material/play_arrow:", type="primary")
    restart = row.toggle("처음부터 다시", help="저장된 상태와 작업본을 지우고 처음부터 실행합니다.")
    if run:
        from sr_agent.workflow.graph import run_ticket

        with st.status(":shimmer[티켓을 처리하고 있습니다]", expanded=False) as status:
            started = time.monotonic()

            def progress(message: str) -> None:
                elapsed = ui.format_duration(time.monotonic() - started)
                status.update(label=f":shimmer[{message}] · {elapsed}")
                status.write(f"`{elapsed}` {message}")

            from sr_agent.llm import LLMQuotaExceeded

            try:
                if ticket.demo_case:  # 시연 케이스는 결함을 심은 작업본에서 돈다 (처음부터 다시면 결함 버전부터 새로)
                    ticket = ui.register_demo(demo.prepare_demo(ticket.demo_case, reset=restart))
                state = run_ticket(ticket, _client(), restart=restart and not ticket.demo_case, progress=progress)
            except (ValueError, LLMQuotaExceeded) as e:
                status.update(label=str(e), state="error")
                return False
            ok = state.status == "approval_pending"
            status.update(label=f"완료: {ui.STATUS_LABELS[state.status]} · "
                                f"{ui.format_duration(time.monotonic() - started)}",
                          state="complete" if ok else "error")
        st.session_state["show_process"] = True
        return True
    return False


# ---------------- 결론: 판정과 핵심 수치 ----------------

def verdict(state: TicketState, summary: dict) -> None:
    last = state.attempts[-1] if state.attempts else None
    if state.human_review_reason:
        st.warning(state.human_review_reason, title="사람 검토 필요", icon=":material/person_alert:")
    if not (last and last.judge):
        return
    checks = last.judge.checks
    ok = last.judge.passed
    st.subheader(f"판정 {'통과' if ok else '실패'} · 시도 {last.number}회째",
                 icon=":material/verified:" if ok else ":material/gpp_bad:", help=HELP["verdict"])

    k = ui.kpis(state)
    evaluated = [c for c in checks if c.passed is not None]
    with st.container(horizontal=True):
        st.metric("판정 조건", f"{sum(c.passed for c in evaluated)}/{len(evaluated)}", border=True)
        st.metric("재시도", f"{k['retries']}회", border=True)
        st.metric("기존 테스트", "-" if k["tests_passed"] is None else f"{k['tests_passed']:,}개 통과", border=True)
        st.metric("공격 발견", f"미달 {k['attack_under_fix']} · 회귀 {k['attack_regression']}", border=True,
                  help=f"공격 에이전트가 찾아 원본과 다르게 동작함을 코드로 확인한 수 (모든 시도 합계). 정답 구현과 "
                       f"비교해 판정이 실제 문제로 잡은 수: 미달 {k['judge_under_fix']}, 회귀 {k['judge_regression']}")
        st.metric("처리 시간", ui.format_duration(summary["seconds"]), border=True)
        st.metric("토큰", ui.format_tokens(summary["tokens"] or state.tokens_used), border=True)

    pending = [c for c in checks if c.passed is None]
    if pending:
        names = ", ".join(ui.CHECK_LABELS.get(c.name, c.name) for c in pending)
        st.info(f"{names}. 비교할 기준(정답 구현·입력 생성 규칙 등)이 없어 확인하지 못했습니다. 승인 전에 패치 diff와 "
                "공격 로그를 직접 확인하세요.", title=f"자동으로 확인하지 못한 조건 {len(pending)}개",
                icon=":material/visibility:")

    failed = [c for c in checks if c.passed is False]
    label = "판정 조건 상세" + (f" · 실패 {len(failed)}개" if failed else "")
    with st.expander(label, expanded=bool(failed), icon=":material/rule:"):
        for c in sorted(checks, key=lambda c: c.passed is not False):  # 실패한 조건을 위로
            st.markdown(f"{CHECK_ICONS[c.passed]} **{ui.CHECK_LABELS.get(c.name, c.name)}**  \n"
                        f":gray[{c.detail}]")


def process(events: list[dict], summary: dict) -> None:
    """처리 과정: 단계별 한 줄 요약. 원본 로그는 토글로만."""
    segs = summary["segments"]
    if not segs:
        return
    turns = sum(s["turns"] for s in segs)
    tools = sum(s["tools"] for s in segs)
    label = f"처리 과정 · {ui.format_duration(summary['seconds'])} · LLM {turns}턴 · 도구 {tools}회"
    with st.expander(label, expanded=st.session_state.pop("show_process", False), icon=":material/timeline:"):
        st.dataframe(
            [{"단계": s["stage"], "시도": s["attempt"], "소요": ui.format_duration(s["seconds"]),
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
        if not state.prediction:
            st.caption("예측 결과가 없습니다.")
        else:
            final = a.final_method if a else None
            st.dataframe(
                [{"순위": i, "함수": p["method"], "점수": round(p["score"], 3),
                  "주요 근거": (p.get("reasons") or [{}])[0].get("label", "-"),
                  "최종 선택": p["method"] == final} for i, p in enumerate(state.prediction, 1)],
                hide_index=True, alt="결함 위치 예측 Top-3",
            )
            with st.expander("SHAP 근거 상세", icon=":material/insights:"):
                for i, p in enumerate(state.prediction, 1):
                    st.markdown(f"**{i}. `{p['method']}`**")
                    st.dataframe([{"근거": r["label"], "값": r["value"], "기여도": round(r["contribution"], 3)}
                                  for r in p.get("reasons", [])], hide_index=True, alt=f"{p['method']} SHAP 근거")
    with right:
        st.markdown("##### 에이전트 최종 판단")
        if a is None:
            st.caption("분석 결과가 없습니다.")
            return
        rank = f"모델 {a.model_rank}위" if a.model_rank else "모델 Top-k 밖"
        st.success(f"`{a.final_method}` · {rank}", title="결함 위치", icon=":material/my_location:")
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
        st.dataframe([{"종류": EVIDENCE_KINDS.get(e.kind, e.kind), "대상": e.ref, "내용": e.note}
                      for e in state.analysis.evidence], hide_index=True, alt="분석 근거 목록",
                     column_config={"내용": st.column_config.TextColumn(width="large")})


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
            st.code(v["before"], language="python", line_numbers=True)
        with right:
            st.markdown("**수정 후**")
            st.code(v["after"], language="python", line_numbers=True)
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
    verified = rep.verified()
    st.markdown("##### 공격 요약", help=HELP["attack"])
    with st.container(horizontal=True):
        st.metric("라운드", f"{len(rep.rounds)}/{rep.planned_rounds}회" if rep.rounds else "-", border=True)
        for kind in ("under_fix", "regression"):
            st.metric(KIND_LABELS[kind], f"{sum(f.kind == kind for f in verified)}건", border=True)
        st.metric("공격 이력 저장", f"{rep.saved_to_memory}건", border=True)
    if rep.escalation:
        st.caption(f":material/trending_up: 라운드를 늘린 이유: {rep.escalation}")

    if att.judge:
        for c in att.judge.checks:
            if c.data.get("under_fix") or c.data.get("regression"):
                st.markdown(f"{CHECK_ICONS[c.passed]} 판정 **{ui.CHECK_LABELS.get(c.name, c.name)}**: "
                            f"미달 {c.data.get('under_fix', 0)} · 회귀 {c.data.get('regression', 0)}")

    if rep.findings:
        st.markdown("##### 발견한 입력")
        st.dataframe(
            [{"분류": KIND_LABELS[f.kind], "확인": f.verified, "입력": f.input_json, "기대": f.expected_json,
              "수정본": str(f.patched), "원본": str(f.original), "근거": f.rule_id, "설명": f.explanation}
             for f in rep.findings],
            hide_index=True, alt="공격 발견 목록",
            column_config={"확인": st.column_config.CheckboxColumn(),
                           "설명": st.column_config.TextColumn(width="large")},
        )
    else:
        st.caption("이 시도에서 공격이 찾은 입력이 없습니다.")
    if rep.rounds:
        with st.expander("라운드별 결과", icon=":material/swords:"):
            st.dataframe([{"관점": r.label, "발견": r.findings, "확인": r.verified, "요약": r.error or r.summary}
                          for r in rep.rounds], hide_index=True, alt="공격 라운드별 결과",
                         column_config={"요약": st.column_config.TextColumn(width="large")})


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
        st.markdown("**담당자 승인**", help=HELP["approval"])
        row = st.container(horizontal=True, vertical_alignment="center")
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
    if ticket.demo_case:
        demo_panel(ticket)
    stage_slot = st.empty()
    stage_slot.markdown(_stage_line(ui.stage_status(state) if state else _initial_stages(ticket)))
    if run_controls(replay, ticket, events, stage_slot):
        st.rerun()
    stage_slot.markdown(_stage_line(ui.stage_status(state) if state else _initial_stages(ticket)))
    if state is None:
        st.info("아직 실행하지 않은 티켓입니다. 실행을 누르세요.", icon=":material/info:")
        return
    summary = ui.run_summary(events)
    verdict(state, summary)
    process(events, summary)
    tabs = st.tabs([":material/my_location: 결함 위치", ":material/menu_book: 근거",
                    ":material/difference: 패치 diff", ":material/swords: 공격 로그"])
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
    if ticket.demo_case:
        answer_section(state, state_path, replay)


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
