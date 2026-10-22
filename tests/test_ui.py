"""화면 (F6, F5-6): 진행 단계, KPI, 승인·반려, 티켓 등록, 재생 모드, Streamlit 렌더링(AppTest)."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from sr_agent import config, ui
from sr_agent.workflow.state import (
    AnalysisOutput,
    AttackReport,
    Evidence,
    FixAttempt,
    FixOutput,
    JudgeCheck,
    JudgeResult,
    Ticket,
    TicketState,
    VerifiedFinding,
)

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
TICKET = Ticket(ticket_id="SR-2041", scenario="S1", type="defect", title="쿠폰 중복 적용 시 정산 금액 음수",
                body="10만 원 이상 주문에 쿠폰을 두 장 쓰면 정산 금액이 음수로 찍힙니다.")


def _attempt(number, passed, under=0, verified_kinds=()):
    checks = [JudgeCheck(name="existing_tests_pass", passed=True, detail="통과 1145, 실패 0",
                         data={"passed": 1145, "failed": 0}),
              JudgeCheck(name="reference_property", passed=passed, detail="...",
                         data={"examples": 200, "mismatches": under, "under_fix": under, "regression": 0})]
    findings = [VerifiedFinding(kind=k, method="discount.apply_coupon", input_json="[1]", expected_json="0",
                                rule_id="BIZ-CPN-02", explanation="", verified=True) for k in verified_kinds]
    return FixAttempt(number=number, fix=FixOutput(method="discount.apply_coupon", patch_summary=f"시도 {number}",
                                                   rule_ids=["BIZ-CPN-02"], history_ids=["HIS-1"]),
                      diff=f"--- a\n+++ b\n-x\n+y{number}\n", attack=AttackReport(summary="공격", findings=findings),
                      judge=JudgeResult(passed=passed, checks=checks))


def _state(status="approval_pending", attempts=None) -> TicketState:
    analysis = AnalysisOutput(final_method="discount.apply_coupon", model_top3=["settlement.settle_order"],
                              model_rank=2, reason="원인", disagreement="증상 위치일 뿐",
                              evidence=[Evidence(kind="rule", ref="BIZ-CPN-01", note="")], history_ids=["HIS-1"],
                              repro_test_path="repro_tests/test_x.py")
    attempts = attempts if attempts is not None else [_attempt(1, False, under=6, verified_kinds=["under_fix"]),
                                                      _attempt(2, True)]
    return TicketState(ticket=TICKET, status=status, analysis=analysis, repro_verified=True, attempts=attempts,
                       prediction=[{"method": "settlement.settle_order", "score": -0.4,
                                    "reasons": [{"feature": "loc", "label": "함수 길이", "value": 20.0,
                                                 "contribution": 0.5}]}],
                       tokens_used=123, run_ids=["SR-2041-1"])


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "REPLAY_DIR", tmp_path / "replay")
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(config, "REPLAY_EVENT_DELAY_S", 0)
    monkeypatch.setenv(config.LIVE_PASSWORD_ENV, "")  # .env에 비밀번호가 있어도 테스트는 열린 상태로
    return tmp_path


# ---------------- 로직 ----------------

def test_stage_status():
    assert dict(ui.stage_status(_state())) == {"분석": "done", "수정": "done", "공격": "done", "판정": "done",
                                                "승인": "current"}
    failed = _state("human_review", [_attempt(1, False)])
    assert dict(ui.stage_status(failed))["판정"] == "failed"
    standardize = _state().model_copy(update={"ticket": TICKET.model_copy(update={"type": "standardize"})})
    assert dict(ui.stage_status(standardize))["분석"] == "skipped"
    new = TicketState(ticket=TICKET, status="analyzing")
    assert dict(ui.stage_status(new)) == {"분석": "current", "수정": "pending", "공격": "pending", "판정": "pending",
                                          "승인": "pending"}


def test_kpis():
    k = ui.kpis(_state())
    assert k == {"retries": 1, "attack_under_fix": 1, "attack_regression": 0, "judge_under_fix": 6,
                 "judge_regression": 0, "tests_passed": 1145}


def test_decide_approve_and_reject(tmp_path):
    path = tmp_path / "state.json"
    state = _state()
    with pytest.raises(ValueError, match="반려 사유"):
        ui.decide(state, path, approve=False, reason="  ")
    ui.decide(state, path, approve=True)
    saved = TicketState.load(path)
    assert saved.status == "approved" and saved.decision.status == "approved"
    assert Path(saved.decision.result_path).read_text(encoding="utf-8") == state.attempts[-1].diff
    with pytest.raises(ValueError, match="이미 결정"):
        ui.decide(saved, path, approve=False, reason="다시")

    failed = _state("human_review", [_attempt(1, False)])
    assert not ui.can_approve(failed)
    with pytest.raises(ValueError, match="판정을 통과한"):
        ui.decide(failed, tmp_path / "s2.json", approve=True)
    ui.decide(failed, tmp_path / "s2.json", approve=False, reason="경계값 처리 미흡")
    assert TicketState.load(tmp_path / "s2.json").decision.reason == "경계값 처리 미흡"


def test_register_ticket(dirs):
    first = ui.register_ticket("defect", "환불 오류", "부분 환불 금액이 이상합니다.")
    second = ui.register_ticket("security", "SQL 진단", "이메일 조회 SQL 조립", "customer_dao.find_by_email")
    assert first.ticket_id == "SR-9001" and second.ticket_id == "SR-9002"
    assert second.method == "customer_dao.find_by_email"
    assert [t.ticket_id for t in ui.registered_tickets()] == ["SR-9001", "SR-9002"]
    assert {"SR-2041", "SR-9002"} <= {t.ticket_id for t in ui.all_tickets()}
    for args, message in [(("unknown", "a", "b"), "유형"), (("defect", "", "b"), "제목"),
                          (("standardize", "a", "b"), "대상 함수"), (("defect", "a", "가" * 5000), "최대")]:
        with pytest.raises(ValueError, match=message):
            ui.register_ticket(*args)


def test_save_and_load_replay(dirs):
    live = config.RUNS_DIR / "SR-2041"
    live.mkdir(parents=True)
    _state("approved").model_copy(update={"decision": None}).save(live / config.STATE_FILENAME)
    events = [{"step": "analysis", "kind": "llm", "turn": 1, "output_summary": "read_method"},
              {"step": "judge", "kind": "judge", "attempt": 1, "passed": True, "failed": []}]
    (config.RUNS_DIR / "SR-2041-1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    ui.save_replay("SR-2041")
    assert ui.replay_tickets() == ["SR-2041"]
    state, work, loaded = ui.load_replay("SR-2041")
    assert loaded == events and state.status == "approval_pending"  # 재생은 결정 전 상태에서 시작
    ui.decide(state, work, approve=False, reason="시연")
    again, _, _ = ui.load_replay("SR-2041")
    assert again.status == "rejected"  # 사본에 기록됨
    assert TicketState.load(ui.replay_source("SR-2041") / config.STATE_FILENAME).status == "approval_pending"
    reset, _, _ = ui.load_replay("SR-2041", reset=True)
    assert reset.status == "approval_pending"


def test_event_messages():
    assert ui.event_message({"step": "analysis", "kind": "llm", "turn": 3, "output_summary": "final"}) == \
        "[분석] 3턴 — 결과 정리"
    assert "read_method" in ui.event_message({"step": "fix", "kind": "tool", "tool": "read_method", "input": "{}"})
    assert ui.event_message({"step": "judge", "kind": "judge", "attempt": 1, "passed": False,
                             "failed": ["reference_property"]}).endswith("실패 (정답 구현과 출력 동일 (Hypothesis))")
    assert ui.event_message({"step": "x", "kind": "unknown"}) is None


def test_cited_ids():
    s = _state()
    assert ui.cited_rule_ids(s) == ["BIZ-CPN-01", "BIZ-CPN-02"]
    assert ui.cited_history_ids(s) == ["HIS-1"]


# ---------------- 화면 렌더링 (AppTest) ----------------

def _replay_app(dirs, state: TicketState) -> AppTest:
    dest = config.REPLAY_DIR / state.ticket.ticket_id
    dest.mkdir(parents=True)
    state.save(dest / config.STATE_FILENAME)
    (dest / "events.jsonl").write_text(json.dumps({"step": "fix", "kind": "llm", "turn": 1,
                                                   "output_summary": "final"}) + "\n", encoding="utf-8")
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def test_app_replay_renders_and_approves(dirs):
    at = _replay_app(dirs, _state())
    assert at.main.header[0].value.startswith("SR-2041")
    assert [t.label.split(": ")[-1] for t in at.tabs] == ["요약", "결함 위치", "근거", "패치 diff", "공격 로그",
                                                          "판정 조건", "처리 과정"]
    assert any("판정 통과" in s.value for s in at.subheader)
    assert not at.metric  # 결론 카드(판정 조건 수·시간·토큰)는 없애고 한 줄 요약으로
    assert any("재시도 1회 · 공격이" in m.value for m in at.markdown)
    assert any(not t.value.empty for t in at.table)  # 요약 탭: 항목별 한 줄 표
    assert not any(e.label.startswith(("판정 조건", "처리 과정")) for e in at.expander)  # 세로로 쌓지 않고 탭으로
    approve = next(b for b in at.button if b.label == "승인 후 반영")
    assert not approve.disabled
    approve.click().run()
    assert not at.exception
    assert TicketState.load(ui.replay_working_path("SR-2041")).status == "approved"
    assert any("승인됨" in s.proto.title for s in at.success)


def test_app_approve_disabled_when_judge_failed(dirs):
    at = _replay_app(dirs, _state("human_review", [_attempt(1, False, under=3)]))
    approve = next(b for b in at.button if b.label == "승인 후 반영")
    assert approve.disabled
    assert any("사람 검토" in w.value for w in at.warning) is False  # 사유 없는 사람 검토 상태
    assert any("실패: " in e.proto.title for e in at.error)  # 실패한 판정 조건은 요약 탭에 바로 보인다
    at.text_area[0].input("정답과 다름").run()
    next(b for b in at.button if b.label == "반려 확정").click().run()
    assert TicketState.load(ui.replay_working_path("SR-2041")).decision.reason == "정답과 다름"


def test_app_replay_button_shows_summary_not_raw_log(dirs):
    """재생 중에는 진행 표시만, 단계별 요약은 처리 과정 탭에. 원본 로그는 토글을 켜야 보인다."""
    at = _replay_app(dirs, _state())
    next(b for b in at.button if "재생" in b.label).click().run()
    assert not at.exception
    assert not any("결과 정리" in c.value for c in at.code)
    assert any(t.label.endswith("처리 과정") for t in at.tabs) and at.dataframe
    at.toggle(key="raw_log").set_value(True).run()
    assert any("결과 정리" in c.value for c in at.code)


def test_app_live_mode_lists_scenarios(dirs):
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.segmented_control[0].set_value("실시간").run()
    assert not at.exception
    labels = at.sidebar.radio[0].options
    assert any("SR-2041" in o for o in labels) and any("SR-2103" in o for o in labels)
    assert any("아직 실행하지 않은" in i.value for i in at.info)


def test_run_summary_groups_stages():
    events = [
        {"ts": "2026-10-21T10:00:00", "step": "analysis", "kind": "llm", "turn": 1},
        {"ts": "2026-10-21T10:00:10", "step": "analysis", "kind": "tool", "tool": "read_method"},
        {"ts": "2026-10-21T10:01:00", "step": "analysis", "kind": "verify", "ok": True},
        {"ts": "2026-10-21T10:02:00", "step": "fix", "kind": "tool", "tool": "apply_patch"},
        {"ts": "2026-10-21T10:03:00", "step": "attack", "kind": "verify", "findings": 0, "verified": 0,
         "rounds": [{}, {}, {}], "planned_rounds": 3},
        {"ts": "2026-10-21T10:04:00", "step": "judge", "kind": "judge", "attempt": 1, "passed": False,
         "failed": ["reference_property"]},
        {"ts": "2026-10-21T10:04:01", "step": "attack", "kind": "memory"},  # 판정 뒤 이력 저장은 구간이 아님
        {"ts": "2026-10-21T10:05:00", "step": "fix", "kind": "llm", "turn": 1},
        {"ts": "2026-10-21T10:06:00", "step": "workflow", "kind": "end", "tokens": 1234},
    ]
    s = ui.run_summary(events)
    assert [(g["stage"], g["attempt"]) for g in s["segments"]] == [("분석", None), ("수정", 1), ("공격", 1),
                                                                    ("판정", 1), ("수정", 2)]
    assert s["segments"][0]["turns"] == 1 and s["segments"][0]["tools"] == 1
    assert s["segments"][0]["seconds"] == 120
    assert s["segments"][1]["result"] == "패치 적용"
    assert s["segments"][2]["result"] == "라운드 3/3회 · 발견 없음"
    assert s["segments"][3]["result"].startswith("실패")
    assert s["seconds"] == 360 and s["tokens"] == 1234
    assert ui.run_summary([]) == {"segments": [], "seconds": None, "tokens": None}


def test_progress_label_and_formatting():
    assert ui.progress_label({"step": "analysis", "kind": "tool", "tool": "predict_fault_location"}) == \
        "분석 · 결함 위치 예측"
    assert ui.progress_label({"step": "workflow", "kind": "end"}) is None
    assert ui.format_duration(344) == "5분 44초" and ui.format_duration(9) == "9초"
    assert ui.format_duration(None) == "-"
    assert ui.format_tokens(529290) == "52.9만" and ui.format_tokens(123) == "123"


def test_event_messages_label_parallel_attack_rounds(tmp_path):
    """동시에 도는 공격 라운드의 턴·도구 줄에 관점을 붙인다 (실행 로그의 angle 필드)."""
    from sr_agent.runlog import RunLog

    log = RunLog("t", tmp_path).tagged(angle="types")
    log.write(step="attack", kind="tool", turn=2, tool="run_diff", input="[]")
    event = json.loads((tmp_path / "t.jsonl").read_text(encoding="utf-8"))
    assert event["angle"] == "types" and event["run_id"] == "t"
    assert ui.event_message(event).startswith("[공격·타입·빈 값]")
    assert ui.event_message({"step": "attack", "kind": "llm", "turn": 1, "output_summary": "final"}) == \
        "[공격] 1턴 — 결과 정리"


def test_app_guide_dialog_and_unevaluated_notice(dirs):
    """? 버튼으로 사용 설명서를 열고, 미평가 조건이 있으면 승인 전 직접 확인하라고 알린다."""
    attempt = _attempt(1, True)
    attempt.judge.checks.append(JudgeCheck(name="attack_findings_rechecked", passed=None,
                                           detail="정답 구현 없음 — 공격 입력 재판정 미평가"))
    at = _replay_app(dirs, _state(attempts=[attempt]))
    assert any("비교할 기준이 없어 확인하지 못했습니다" in i.value for i in at.info)
    assert any("조건부 통과 (미평가 1개)" in s.value for s in at.subheader)
    next(b for b in at.button if b.label == "사용 설명서").click().run()
    assert not at.exception
    assert any("RedFix가 하는 일" in m.value for m in at.markdown)


def test_apply_diff_and_code_versions(dirs):
    """재생 데이터처럼 diff만 있어도 원본 파일에 적용해 대상 함수의 수정 전/후 전체 코드를 만든다."""
    import difflib

    rel = "src/order_settlement/calc.py"
    before = "import math\n\n\ndef add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
    after = ("import math\nimport os\n\n\ndef add(a, b):\n    return max(0, a + b)\n\n\n"
             "def sub(a, b):\n    return a - b\n")
    diff = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                        fromfile=f"a/{rel}", tofile=f"b/{rel}", n=2))
    assert ui.apply_diff(before, diff, rel) == after
    assert ui.apply_diff(before.replace("a + b", "a * b"), diff, rel) is None  # 다른 원본에서 만든 diff

    original = config.RUNS_DIR / "SR-2041" / config.ORIGINAL_DIRNAME / rel
    original.parent.mkdir(parents=True)
    original.write_text(before, encoding="utf-8")
    attempt = FixAttempt(number=1, fix=FixOutput(method="calc.add", patch_summary="", rule_ids=[], history_ids=[]),
                         diff=diff)
    state = TicketState(ticket=TICKET, attempts=[attempt])
    v = ui.code_versions(state, attempt)
    assert v["before"] == "def add(a, b):\n    return a + b"
    assert v["after"] == "def add(a, b):\n    return max(0, a + b)"
    assert v["imports"] == {"added": ["import os"], "removed": []}
    assert ui.changed_line_numbers(v["before"], v["after"]) == [2]
    assert ui.current_code(state.model_copy(update={"analysis": None, "ticket": TICKET.model_copy(
        update={"method": "calc.sub"})})) == "def sub(a, b):\n    return a - b"
    stored = attempt.model_copy(update={"before_code": "def add(a, b): ...", "after_code": "def add(a, b): pass"})
    assert ui.code_versions(state, stored)["before"] == "def add(a, b): ..."  # 상태에 저장된 코드가 우선


def test_app_shows_before_after_code(dirs):
    attempt = _attempt(1, True).model_copy(update={"before_code": "def payable(a, d):\n    return a - d",
                                                   "after_code": "def payable(a, d):\n    return max(0, a - d)"})
    at = _replay_app(dirs, _state(attempts=[attempt]))
    codes = [c.value for c in at.code]
    assert "def payable(a, d):\n    return a - d" in codes and "def payable(a, d):\n    return max(0, a - d)" in codes
    at.segmented_control(key="view-SR-2041-1").set_value("바뀐 줄 표시").run()
    assert not at.exception
    assert any("+    return max(0, a - d)" in c.value for c in at.code)


def test_app_demo_case_flow(dirs, monkeypatch):
    """실시간 모드: 시연 케이스를 불러와 선택하고, 확인할 것 안내를 보여 준다 (결함 작업본 준비는 가짜로)."""
    from sr_agent import demo

    fake = Ticket(ticket_id="DEMO-D", type="security", title="이메일 조회 SQL", body="본문",
                  method="customer_dao.find_by_email", scenario="시연 D", demo_case="D")
    monkeypatch.setattr(demo, "prepare_demo", lambda case_id, runs_dir=None, reset=False: fake)
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.segmented_control[0].set_value("실시간").run()
    next(b for b in at.sidebar.button if b.label == "불러오기").click().run()
    assert not at.exception
    assert at.sidebar.radio[0].value == "DEMO-D"
    assert any(e.label.startswith("시연 케이스 D") for e in at.expander)


def test_app_replay_demo_answer_check(dirs):
    """재생 데이터의 정답 확인 결과는 버튼을 누른 뒤에만 보이고, 담당자 판단과의 일치를 알려 준다."""
    ticket = TICKET.model_copy(update={"ticket_id": "DEMO-A", "demo_case": "A", "scenario": "시연 A",
                                       "answer_method": "discount.apply_coupon", "answer_tests": ["tests/x.py::t"]})
    state = _state(attempts=[_attempt(1, True)]).model_copy(update={"ticket": ticket})
    state.answer_check = {"answer_method": "discount.apply_coupon", "agent_method": "discount.apply_coupon",
                          "tests": {"passed": True, "detail": "통과 1, 실패 0"}, "patch_ok": True}
    dest = config.REPLAY_DIR / "DEMO-A"
    dest.mkdir(parents=True)
    state.save(dest / config.STATE_FILENAME)
    (dest / "events.jsonl").write_text("", encoding="utf-8")
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.radio[0].set_value("DEMO-A").run()
    assert not any("정답과 맞습니다" in s.value for s in at.success)
    next(b for b in at.button if b.label == "승인 후 반영").click().run()
    next(b for b in at.button if b.label == "정답 확인").click().run()
    assert not at.exception
    assert any("정답과 맞습니다" in s.value for s in at.success)


# ---------------- 결과 삭제·조건 묶음·백그라운드 실행 (v1.2) ----------------

def test_delete_results_removes_runs_and_registry(dirs):
    ticket = ui.register_ticket("defect", "포인트 중복 적립", "포인트로 결제하면 적립 포인트가 두 번 쌓입니다.")
    tid = ticket.ticket_id
    root = config.RUNS_DIR / tid
    root.mkdir(parents=True)
    TicketState(ticket=ticket, run_ids=[f"{tid}-20261022-100000"]).save(root / config.STATE_FILENAME)
    (config.RUNS_DIR / f"{tid}-20261022-100000.jsonl").write_text("{}", encoding="utf-8")
    (config.RUNS_DIR / f"{tid}-20261022-090000.jsonl").write_text("{}", encoding="utf-8")  # 상태에 없는 끊긴 실행
    other = config.RUNS_DIR / "SR-90010-20261022-100000.jsonl"  # 비슷한 이름의 다른 티켓 로그는 남긴다
    other.write_text("{}", encoding="utf-8")
    assert tid in ui.tickets_with_results()
    result = ui.delete_results(tid, remove_ticket=True)
    assert result == {"dirs": 1, "logs": 2, "ticket_removed": True}
    assert not root.exists() and other.exists() and not ui.is_registered(tid)
    # 시연 S1~S3는 결과만 지우고 목록에는 남는다
    (config.RUNS_DIR / "SR-2041").mkdir()
    _state().save(config.RUNS_DIR / "SR-2041" / config.STATE_FILENAME)
    assert ui.delete_results("SR-2041", remove_ticket=True)["ticket_removed"] is False
    assert any(t.ticket_id == "SR-2041" for t in ui.all_tickets())
    with pytest.raises(ValueError):
        ui.delete_results("../etc")


def test_grouped_checks_cover_every_label():
    from sr_agent.workflow.state import JudgeCheck

    checks = [JudgeCheck(name=n, passed=True, detail="") for n in ui.CHECK_LABELS]
    checks.append(JudgeCheck(name="new_check", passed=None, detail=""))
    groups = dict(ui.grouped_checks(checks))
    assert [c.name for c in groups["공격 결과 재확인"]][0] == "attack_findings_rechecked"
    assert [c.name for c in groups["기타"]] == ["new_check"]
    assert set(ui.CHECK_HELP) == set(ui.CHECK_LABELS)  # 모든 조건에 설명이 있다


def test_job_manager_runs_in_background_once():
    import threading

    from sr_agent.jobs import JobManager

    gate = threading.Event()
    jobs = JobManager()

    def work(progress):
        progress("분석 에이전트 실행")
        gate.wait(5)
        return "approval_pending"
    job = jobs.start("SR-1", work)
    assert jobs.running("SR-1")
    with pytest.raises(ValueError, match="이미 실행 중"):
        jobs.start("SR-1", work)
    jobs.forget("SR-1")
    assert jobs.get("SR-1") is job  # 실행 중에는 지우지 않는다
    gate.set()
    for _ in range(100):
        if job.done:
            break
        threading.Event().wait(0.05)
    assert job.done and job.result == "approval_pending" and job.last_message == "분석 에이전트 실행"

    failed = jobs.start("SR-2", lambda progress: (_ for _ in ()).throw(RuntimeError("크레딧 소진")))
    for _ in range(100):
        if failed.done:
            break
        threading.Event().wait(0.05)
    assert failed.error == "크레딧 소진" and not jobs.running("SR-2")


def test_app_live_run_survives_interaction(dirs, monkeypatch):
    """실행은 백그라운드에서 돈다: 실행 중 다른 티켓을 골라도 끊기지 않고, 돌아오면 진행 화면, 끝나면 결과."""
    import threading

    from sr_agent import llm
    from sr_agent.workflow import graph

    gate, calls = threading.Event(), []

    def fake_run(ticket, client, restart=False, progress=lambda m: None, **kw):
        calls.append(ticket.ticket_id)
        progress("분석 에이전트 실행 (1/3)")
        gate.wait(20)
        state = _state().model_copy(update={"ticket": ticket})
        path = ui.live_state_path(ticket.ticket_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        state.save(path)
        return state
    monkeypatch.setattr(graph, "run_ticket", fake_run)
    monkeypatch.setattr(llm, "get_client", lambda: None)
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.segmented_control[0].set_value("실시간").run()
    at.sidebar.radio[0].set_value("SR-2077").run()
    next(b for b in at.button if b.label == "실행").click().run()
    assert not at.exception
    assert any("서버에서 처리 중" in c.value for c in at.caption)
    at.sidebar.radio[0].set_value("SR-2041").run()  # 다른 티켓을 봐도
    at.sidebar.radio[0].set_value("SR-2077").run()
    assert any("서버에서 처리 중" in c.value for c in at.caption) and calls == ["SR-2077"]  # 같은 실행이 계속
    gate.set()
    for _ in range(100):
        if ui.live_state_path("SR-2077").is_file():
            break
        threading.Event().wait(0.05)
    threading.Event().wait(0.3)
    at.run()
    assert not at.exception
    assert any("판정 통과" in s.value for s in at.subheader) and calls == ["SR-2077"]


def test_finding_verdict_and_attack_summary():
    """공격의 미달·회귀는 주장일 뿐: 판정이 다시 실행해 정답과 다를 때만 실제 문제로 보여 준다."""
    from sr_agent.tools.attack import input_key

    def finding(inp):
        return VerifiedFinding(kind="under_fix", method="discount.apply_coupon", input_json=inp, expected_json="0",
                               rule_id="BIZ-CPN-02", explanation="", verified=True)
    real, fine, broken = finding("[1, 2]"), finding("[3, 4]"), finding("not json")
    check = JudgeCheck(name="attack_findings_rechecked", passed=False, detail="",
                       data={"mismatches": 1, "failed_keys": [input_key("discount.apply_coupon", [1, 2])]})
    att = FixAttempt(number=1, attack=AttackReport(findings=[real, fine, broken]),
                     judge=JudgeResult(passed=False, checks=[check]))
    assert [ui.finding_verdict(att, f) for f in (real, fine, broken)] == ["실제 문제", "문제 아님", "실행 불가"]
    state = TicketState(ticket=TICKET, attempts=[att])
    assert ui.attack_summary(state) == "공격이 제기한 문제 3건 → 판정이 다시 실행해 실제 문제 1건"
    unjudged = att.model_copy(update={"judge": JudgeResult(passed=True, checks=[
        check.model_copy(update={"passed": None})])})
    assert ui.finding_verdict(unjudged, real) == "미평가"
    assert "자동 재확인 못함" in ui.attack_summary(TicketState(ticket=TICKET, attempts=[unjudged]))
    assert ui.format_result({"ok": True, "value": -5}) == "-5"
    assert ui.format_result({"ok": False, "exception": "ValueError: x"}) == "예외: ValueError: x"


def test_app_delete_button_flow(dirs):
    """실행 버튼 줄의 삭제 → 확인 창 → 결과·등록 삭제. 결과가 없는 S1~S3는 비활성, 사이드바 결과 정리도 동작."""
    ticket = ui.register_ticket("defect", "포인트 중복 적립", "포인트로 결제하면 적립 포인트가 두 번 쌓입니다.")
    tid = ticket.ticket_id
    for t in (tid, "SR-2077"):
        root = config.RUNS_DIR / t
        root.mkdir(parents=True)
        _state().model_copy(update={"ticket": ticket if t == tid else ui.scenario_tickets()[1],
                                    "run_ids": [f"{t}-20261022-100000"]}).save(root / config.STATE_FILENAME)
        (config.RUNS_DIR / f"{t}-20261022-100000.jsonl").write_text("", encoding="utf-8")
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.segmented_control[0].set_value("실시간").run()
    at.sidebar.radio[0].set_value("SR-2041").run()
    assert at.button(key="delete_results").disabled  # 결과 없는 시연 티켓
    at.sidebar.radio[0].set_value(tid).run()
    delete = at.button(key="delete_results")
    assert not delete.disabled
    delete.click().run()
    assert not at.exception
    assert next(c for c in at.checkbox if c.label == "티켓도 목록에서 삭제").value is True  # 확인 창이 열림
    at.button(key="delete_cancel").click().run()  # 취소하면 창이 닫히고 아무것도 지우지 않는다
    assert (config.RUNS_DIR / tid).exists() and not any(c.label == "티켓도 목록에서 삭제" for c in at.checkbox)
    at.button(key="delete_results").click().run()
    at.button(key="delete_confirm").click().run()
    assert not at.exception
    assert not (config.RUNS_DIR / tid).exists() and not (config.RUNS_DIR / f"{tid}-20261022-100000.jsonl").exists()
    assert not ui.is_registered(tid) and not any(o.startswith(tid) for o in at.sidebar.radio[0].options)
    # 사이드바 결과 정리: S2 결과를 지워도 S2 티켓은 목록에 남는다
    at.sidebar.multiselect[0].select("SR-2077").run()
    next(b for b in at.sidebar.button if b.label == "선택한 결과 삭제").click().run()
    assert not at.exception and not (config.RUNS_DIR / "SR-2077").exists()
    assert any(o.startswith("SR-2077") for o in at.sidebar.radio[0].options)


def test_app_live_mode_password(dirs, monkeypatch):
    """비밀번호가 설정돼 있으면 실시간 모드(API 호출)는 맞힐 때까지 티켓을 보여 주지 않는다. 재생 모드는 그대로."""
    monkeypatch.setenv(config.LIVE_PASSWORD_ENV, "s3cret")
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.segmented_control[0].set_value("실시간").run()
    assert not at.sidebar.radio and at.sidebar.text_input[0].label == "실시간 모드 비밀번호"
    at.sidebar.text_input[0].input("wrong")
    at.sidebar.button[0].click().run()
    assert any("비밀번호가 다릅니다" in e.value for e in at.sidebar.error) and not at.sidebar.radio
    at.sidebar.text_input[0].input("s3cret")
    at.sidebar.button[0].click().run()
    assert not at.exception and any(o.startswith("SR-2041") for o in at.sidebar.radio[0].options)


def test_replay_copy_refreshes_after_rerecording(dirs):
    """재생 데이터를 다시 녹화하면 예전 사본 대신 새 녹화를 보여 준다. 재생 중 승인·반려한 사본은 유지."""
    import os

    dest = config.REPLAY_DIR / "SR-2041"
    dest.mkdir(parents=True)
    _state().save(dest / config.STATE_FILENAME)
    state, work, _ = ui.load_replay("SR-2041")
    ui.decide(state, work, approve=False, reason="시연")
    assert ui.load_replay("SR-2041")[0].status == "rejected"  # 사본이 더 새로우면 그대로
    newer = _state().model_copy(update={"tokens_used": 777})
    newer.save(dest / config.STATE_FILENAME)
    future = work.stat().st_mtime + 10
    os.utime(dest / config.STATE_FILENAME, (future, future))
    again = ui.load_replay("SR-2041")[0]
    assert again.tokens_used == 777 and again.status == "approval_pending"


def test_overview_rows_one_line_per_item():
    """요약 탭의 표: 결함 위치·수정·근거·공격·판정 조건을 한 줄씩."""
    state = _state()
    rows = {r["항목"]: r["내용"] for r in ui.overview_rows(state)}
    assert set(rows) >= {"결함 위치", "수정", "공격", "판정 조건"}
    assert rows["결함 위치"].startswith("discount.apply_coupon (모델")
    assert "시도 2회째 (재시도 1회)" in rows["판정 조건"]
    assert all("\n" not in v for v in rows.values())
    assert ui.first_sentence("첫 문장입니다. 둘째 문장입니다.") == "첫 문장입니다."
    assert ui.first_sentence("가" * 300).endswith("…") and len(ui.first_sentence("가" * 300)) == 140


def test_app_needs_info_has_no_decision(dirs):
    """보완 요청 티켓은 요약 탭에 사유만 보이고 승인·반려 버튼은 없다."""
    from sr_agent.workflow.state import IntakeResult

    reason = "시스템 수정 요청으로 볼 수 없음"
    state = TicketState(ticket=TICKET, status="needs_info", human_review_reason=f"접수 보완 요청: {reason}",
                        intake=IntakeResult(accepted=False, problems=[reason]))
    at = _replay_app(dirs, state)
    assert any("보완 요청" in e.proto.title for e in at.error)
    assert not any(b.label == "승인 후 반영" for b in at.button)
