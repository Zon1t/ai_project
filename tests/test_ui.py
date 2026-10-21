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
    assert at.main.title[0].value.startswith("SR-2041")
    assert len(at.tabs) == 4 and [t.label.split(": ")[-1] for t in at.tabs] == ["결함 위치", "근거", "패치 diff",
                                                                                 "공격 로그"]
    assert any("판정 통과" in s.value for s in at.subheader)
    assert any(m.label == "판정 조건" for m in at.metric)  # 결론 카드를 본문 위쪽에
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
    assert any("실패" in e.label for e in at.expander)  # 실패한 판정 조건은 펼쳐서 보여 준다
    at.text_area[0].input("정답과 다름").run()
    next(b for b in at.button if b.label == "반려 확정").click().run()
    assert TicketState.load(ui.replay_working_path("SR-2041")).decision.reason == "정답과 다름"


def test_app_replay_button_shows_summary_not_raw_log(dirs):
    """재생 중에는 진행 표시만, 끝나면 처리 과정 요약을 펼친다. 원본 로그는 토글을 켜야 보인다."""
    at = _replay_app(dirs, _state())
    next(b for b in at.button if "재생" in b.label).click().run()
    assert not at.exception
    assert not any("결과 정리" in m.value for m in at.markdown)
    process = next(e for e in at.expander if e.label.startswith("처리 과정"))
    assert process.proto.expanded
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
    assert any("자동으로 확인하지 못한 조건 1개" in i.proto.title for i in at.info)
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
