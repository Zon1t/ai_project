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

from sr_agent import config, ui  # noqa: E402
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
    "mode": "재생: 미리 실행한 결과를 API 호출 없이 봅니다. 실시간: 티켓을 실제로 처리하고 새 티켓을 등록합니다.",
    "stages": "분석 › 수정 › 공격 › 판정 › 승인 순서로 진행합니다. 코드 표준화·보안은 진단이 위치를 알려 주므로 "
              "분석을 생략합니다. 판정에 실패하면 수정부터 다시 합니다 (최대 3회).",
    "verdict": "판정은 LLM이 아니라 코드가 유형별 통과 조건을 검사해 정합니다. 실패한 조건은 재시도하는 수정 "
               "에이전트에 전달됩니다. 회색(미평가)은 비교할 기준이 없어 자동으로 확인하지 못한 조건입니다.",
    "process": "단계별 소요 시간, LLM 호출(턴), 도구 호출 수와 결과입니다. 원본 실행 로그는 토글을 켜야 보입니다.",
    "prediction": "결함 위치 예측 모델(XGBoost Ranker)의 Top-3입니다. 티켓-코드 유사도, 스택트레이스, "
                  "함수 길이·복잡도로 점수를 매기며 참고 자료일 뿐입니다. 최종 위치는 오른쪽 에이전트 판단을 따릅니다.",
    "decision": "분석 에이전트가 예측·호출 관계·과거 이력·재현 테스트로 정한 원인 함수입니다. 모델 1위와 다르게 고르면 "
                "그 이유를 남깁니다.",
    "rules": "수정 근거로 인용한 사내 규칙입니다 (SEC 보안 · DEP 지원 종료 API · STY 팀 스타일 · BIZ 업무 규칙).",
    "history": "비슷했던 과거 장애와 그때의 조치입니다 (학습용 이력만 검색).",
    "evidence": "분석 에이전트가 원인 판단에 쓴 근거입니다. 확인할 수 있는 식별자(규칙 ID, 이력 ID, 함수, "
                "테스트)만 적습니다.",
    "diff": "작업본(runs/<티켓ID>/work/)에서 바뀐 코드입니다. 대상 함수와 같은 파일의 import만 바꿀 수 있습니다.",
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
        tickets = ui.all_tickets()
        if replay:
            saved = set(ui.replay_tickets())
            tickets = [t for t in tickets if t.ticket_id in saved]
        if not tickets:
            st.info("재생할 결과가 없습니다." if replay else "티켓이 없습니다.")
            return replay, None
        by_id = {t.ticket_id: t for t in tickets}

        st.subheader("티켓", divider="gray")
        ids = list(by_id)
        captions = [" · ".join(x for x in (by_id[i].scenario, _status_of(i, replay)) if x) for i in ids]
        selected = st.radio("티켓", ids, key="ticket", label_visibility="collapsed", captions=captions,
                            format_func=lambda tid: f"{tid}  {ui.TYPE_LABELS[by_id[tid].type]}")

        if not replay:
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

            try:
                state = run_ticket(ticket, _client(), restart=restart, progress=progress)
            except ValueError as e:
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
        st.metric("판정 조건", f"{sum(c.passed for c in evaluated)}/{len(evaluated)}", border=True,
                  help="코드로 검사한 통과 조건 수 (LLM이 판단하지 않음)")
        st.metric("재시도", f"{k['retries']}회", border=True, help=f"수정 재시도 상한 {config.MAX_PATCH_RETRIES}회")
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
        st.caption(HELP["process"])
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

def tab_location(state: TicketState) -> None:
    if state.ticket.type != "defect":
        st.caption(f"분석 단계 생략: 진단 결과가 대상 함수 `{state.ticket.method}`를 알려 줍니다.")
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
                column_config={"최종 선택": st.column_config.CheckboxColumn(help="에이전트가 고른 최종 결함 위치")},
            )
            with st.expander("SHAP 근거 상세", icon=":material/insights:"):
                for i, p in enumerate(state.prediction, 1):
                    st.markdown(f"**{i}. `{p['method']}`**")
                    st.dataframe([{"근거": r["label"], "값": r["value"], "기여도": round(r["contribution"], 3)}
                                  for r in p.get("reasons", [])], hide_index=True, alt=f"{p['method']} SHAP 근거")
    with right:
        st.markdown("##### 에이전트 최종 판단", help=HELP["decision"])
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


def tab_evidence(state: TicketState) -> None:
    rules = _rules()
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("##### 인용한 사내 규칙", help=HELP["rules"])
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
        st.markdown("##### 인용한 장애·조치 이력", help=HELP["history"])
        details = ui.history_details(ui.cited_history_ids(state))
        if not details:
            st.caption("인용한 이력이 없거나 RAG 색인이 없습니다.")
        for h in details:
            with st.expander(f"{h['history_id']} · {h['method']}", icon=":material/history:"):
                st.markdown(h["ticket"])
                st.markdown(f"**원인** {h['root_cause']}  \n**조치** {h['fix_summary']}")
    if state.analysis and state.analysis.evidence:
        st.markdown("##### 분석 근거", help=HELP["evidence"])
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
    st.markdown("##### 바뀐 코드", help=HELP["diff"])
    st.code(att.diff or "(변경 없음)", language="diff", line_numbers=True)


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
        st.metric("라운드", f"{len(rep.rounds)}/{rep.planned_rounds}회" if rep.rounds else "-", border=True,
                  help="관점 하나를 맡은 공격 실행 수. 확인된 발견이 나오면 남은 라운드를 건너뜁니다.")
        for kind in ("under_fix", "regression"):
            st.metric(KIND_LABELS[kind], f"{sum(f.kind == kind for f in verified)}건", border=True,
                      help="코드로 다시 실행해 확인한 수")
        st.metric("공격 이력 저장", f"{rep.saved_to_memory}건", border=True,
                  help="판정이 정답과 다르다고 확인한 입력만 저장합니다.")
    if rep.escalation:
        st.caption(f":material/trending_up: 라운드를 늘린 이유: {rep.escalation}")

    if att.judge:
        for c in att.judge.checks:
            if c.data.get("under_fix") or c.data.get("regression"):
                st.markdown(f"{CHECK_ICONS[c.passed]} 판정 **{ui.CHECK_LABELS.get(c.name, c.name)}**: "
                            f"미달 {c.data.get('under_fix', 0)} · 회귀 {c.data.get('regression', 0)}")

    if rep.findings:
        st.markdown("##### 발견한 입력", help="확인 = 코드로 다시 실행해 원본과 다르게 동작함을 확인한 입력")
        st.dataframe(
            [{"분류": KIND_LABELS[f.kind], "확인": f.verified, "입력": f.input_json, "기대": f.expected_json,
              "수정본": str(f.patched), "원본": str(f.original), "근거": f.rule_id, "설명": f.explanation}
             for f in rep.findings],
            hide_index=True, alt="공격 발견 목록",
            column_config={"확인": st.column_config.CheckboxColumn(help="코드로 다시 실행해 확인"),
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
    stage_slot = st.empty()
    stage_slot.markdown(_stage_line(ui.stage_status(state) if state else _initial_stages(ticket)),
                         help=HELP["stages"])
    if run_controls(replay, ticket, events, stage_slot):
        st.rerun()
    stage_slot.markdown(_stage_line(ui.stage_status(state) if state else _initial_stages(ticket)),
                         help=HELP["stages"])
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


main()
