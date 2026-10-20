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
    assert at.title[0].value.startswith("SR-2041")
    assert len(at.tabs) == 4 and [t.label for t in at.tabs] == ["결함 위치", "근거", "패치 diff", "공격 로그"]
    assert any("판정 통과" in s.value for s in at.subheader)
    approve = next(b for b in at.button if b.label == "✅ 승인 후 반영")
    assert not approve.disabled
    approve.click().run()
    assert not at.exception
    assert TicketState.load(ui.replay_working_path("SR-2041")).status == "approved"
    assert any("승인됨" in s.value for s in at.success)


def test_app_approve_disabled_when_judge_failed(dirs):
    at = _replay_app(dirs, _state("human_review", [_attempt(1, False, under=3)]))
    approve = next(b for b in at.button if b.label == "✅ 승인 후 반영")
    assert approve.disabled
    assert any("사람 검토" in w.value for w in at.warning) is False  # 사유 없는 사람 검토 상태
    at.text_area[0].input("정답과 다름").run()
    next(b for b in at.button if b.label == "반려").click().run()
    assert TicketState.load(ui.replay_working_path("SR-2041")).decision.reason == "정답과 다름"


def test_app_replay_button_streams_events(dirs):
    at = _replay_app(dirs, _state())
    next(b for b in at.button if "재생" in b.label).click().run()
    assert not at.exception
    assert any("결과 정리" in m.value for m in at.markdown)


def test_app_live_mode_lists_scenarios(dirs):
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.radio[0].set_value("실시간 실행").run()
    assert not at.exception
    labels = at.sidebar.radio[1].options
    assert any("SR-2041" in o for o in labels) and any("SR-2103" in o for o in labels)
    assert any("아직 실행하지 않은" in i.value for i in at.info)
