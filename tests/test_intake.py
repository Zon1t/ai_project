"""접수 검사(F5-0)·예측 신호(N5①)·티켓 증상 연결 판정 (v1.2). LLM은 가짜 클라이언트."""

import json
from types import SimpleNamespace

import pytest

from sr_agent import config, ui
from sr_agent.agents.context import RunContext
from sr_agent.llm import LLMQuotaExceeded
from sr_agent.tools.predict import judge_signal
from sr_agent.workflow import intake
from sr_agent.workflow.judge import intake_check, symptom_check
from sr_agent.workflow.state import AnalysisOutput, IntakeOutput, IntakeResult, Ticket, TicketState

S1 = Ticket(ticket_id="SR-2041", type="defect", title="쿠폰 중복 적용 시 정산 금액 음수",
            body="10만 원 이상 주문에 쿠폰을 두 장 쓰면 정산 금액이 음수로 찍힙니다.")
NONSENSE = Ticket(ticket_id="SR-9002", type="defect", title="test", body="집가고싶다")


def output(**kw) -> IntakeOutput:
    base = {"is_change_request": True, "in_domain": True,
            "symptom_quote": "쿠폰을 두 장 쓰면 정산 금액이 음수로 찍힙니다",
            "expected_behavior": "정산 금액이 0원 아래로 내려가지 않아야 함", "missing": [], "reason": "증상이 분명함"}
    return IntakeOutput(**{**base, **kw})


# ---------------- 코드 조건 ----------------

def test_rule_problems_short_or_repeated_body():
    assert intake.rule_problems("test", "집가고싶다")  # 5자
    assert intake.rule_problems("t", "ㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋ") == ["본문이 같은 글자의 반복임"]
    assert intake.rule_problems(S1.title, S1.body) == []


def test_quoted_from_requires_verbatim_text():
    assert intake.quoted_from("“쿠폰을 두 장 쓰면  정산 금액이 음수로 찍힙니다.”", S1)  # 공백·따옴표·마침표 차이는 허용
    assert not intake.quoted_from("쿠폰 두 장이면 정산이 음수", S1)  # 고쳐 쓴 문장
    assert not intake.quoted_from("음수", S1)  # 너무 짧음
    assert not intake.quoted_from("", S1)


@pytest.mark.parametrize(("change", "problem"), [
    ({"is_change_request": False}, "수정 요청으로 볼 수 없음"),
    ({"in_domain": False}, "업무와 관련 없음"),
    ({"symptom_quote": "정산 금액 오류 발생"}, "증상(요청) 문장을 찾지 못함"),  # LLM이 지어낸 인용
    ({"expected_behavior": ""}, "올바른 동작"),
])
def test_decide_rejects(change, problem):
    result = intake.decide(S1, output(**change))
    assert not result.accepted and any(problem in p for p in result.problems)


def test_decide_accepts_and_non_defect_needs_no_expected():
    assert intake.decide(S1, output()).accepted
    s3 = Ticket(ticket_id="SR-2103", type="security", title="시큐어코딩 진단",
                body="customer_dao.find_by_name에서 f-string으로 SQL 조립", method="customer_dao.find_by_name")
    assert intake.decide(s3, output(symptom_quote="f-string으로 SQL 조립", expected_behavior="")).accepted


# ---------------- LLM 호출 (가짜 클라이언트) ----------------

class FakeClient:
    def __init__(self, parsed=None, error=None):
        self.calls = []
        self.parsed, self.error = parsed, error
        self.responses = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        usage = SimpleNamespace(input_tokens=100, output_tokens=20, total_tokens=120,
                                input_tokens_details=None, output_tokens_details=None)
        return SimpleNamespace(status="completed", output=[], output_parsed=self.parsed, usage=usage, id="r1")


def _ctx(tmp_path) -> RunContext:
    return RunContext.new("SR-T", runs_dir=tmp_path)


def _log(ctx, tmp_path) -> list[dict]:
    return [json.loads(x) for x in (tmp_path / f"{ctx.log.run_id}.jsonl").read_text(encoding="utf-8").splitlines()]


def test_check_ticket_calls_llm_and_logs(tmp_path):
    client, ctx = FakeClient(parsed=output()), _ctx(tmp_path)
    result = intake.check_ticket(client, S1, ctx)
    assert result.accepted and ctx.budget.used == 120
    call = client.calls[0]
    assert call["model"] == config.MODEL and call["reasoning"] == {"effort": config.INTAKE_REASONING_EFFORT}
    assert call["text_format"] is IntakeOutput and S1.body in call["input"]
    assert [e["kind"] for e in _log(ctx, tmp_path)] == ["llm", "intake"]


def test_check_ticket_rule_failure_skips_llm(tmp_path):
    client, ctx = FakeClient(parsed=output()), _ctx(tmp_path)
    result = intake.check_ticket(client, NONSENSE, ctx)
    assert not result.accepted and result.output is None and client.calls == []
    assert _log(ctx, tmp_path)[-1]["accepted"] is False


def test_check_ticket_quota_error_stops(tmp_path):
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    error = openai.RateLimitError("quota", response=httpx.Response(429, request=request),
                                  body={"code": "insufficient_quota"})
    with pytest.raises(LLMQuotaExceeded):
        intake.check_ticket(FakeClient(error=error), S1, _ctx(tmp_path))


# ---------------- 예측 신호 ----------------

THRESHOLDS = {"top1": -1.85, "emb_max": 0.5875, "tfidf_max": 0.0656}


def test_judge_signal_low_for_out_of_distribution():
    low = judge_signal({"top1": -5.405, "emb_max": 0.486, "tfidf_max": 0.064}, THRESHOLDS)  # "집가고싶다"
    assert low["low"] is True and len(low["reasons"]) == 3
    ok = judge_signal({"top1": -0.445, "emb_max": 0.625, "tfidf_max": 0.101}, THRESHOLDS)  # S1 티켓
    assert ok["low"] is False and ok["reasons"] == []
    assert judge_signal({"top1": 0.0}, {})["low"] is None  # 임계값 파일 없음: 판단 안 함


def test_saved_thresholds_come_from_eval_split():
    saved = json.loads(config.PREDICTION_SIGNAL_PATH.read_text(encoding="utf-8"))
    assert saved["split"] == "test" and saved["percentile"] == config.PREDICTION_SIGNAL_PERCENTILE
    assert set(saved["thresholds"]) == {"top1", "emb_max", "tfidf_max"}


# ---------------- 판정 조건 ----------------

def analysis(**kw) -> AnalysisOutput:
    base = {"final_method": "discount.apply_coupon", "model_top3": [], "model_rank": None, "reason": "",
            "disagreement": "", "evidence": [], "history_ids": [], "repro_test_path": "repro_tests/test_x.py",
            "ticket_symptom": "쿠폰을 두 장 쓰면 정산 금액이 음수로 찍힙니다", "symptom_check": "결제 금액 == 0",
            "confident": True, "blocker": ""}
    return AnalysisOutput(**{**base, **kw})


def test_symptom_check_pass_fail_and_old_records():
    assert symptom_check(S1, analysis()).passed is True
    assert symptom_check(S1, analysis(ticket_symptom="쿠폰 할인 계산 오류")).passed is False
    assert symptom_check(S1, analysis(confident=False, blocker="규칙 없음")).passed is False
    assert symptom_check(S1, analysis(ticket_symptom="")).passed is None  # v1.2 이전 분석


def test_intake_check_reflects_saved_result():
    assert intake_check(IntakeResult(accepted=True, output=output())).passed is True
    check = intake_check(IntakeResult(accepted=False, problems=["본문이 너무 짧음"]))
    assert check.passed is False and "본문이 너무 짧음" in check.detail


def test_old_state_without_new_fields_loads(tmp_path):
    """v1.2 이전에 저장한 상태(접수 검사·분석 새 필드 없음)도 그대로 불러온다."""
    data = TicketState(ticket=S1, analysis=analysis()).model_dump(mode="json")
    data.pop("intake"), data.pop("prediction_signal")
    for key in ("ticket_symptom", "symptom_check", "confident", "blocker"):
        data["analysis"].pop(key)
    path = tmp_path / "state.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    state = TicketState.load(path)
    assert state.intake is None and state.prediction_signal is None
    assert state.analysis.confident is True and state.analysis.ticket_symptom == ""
    assert symptom_check(S1, state.analysis).passed is None  # 판정에서는 미평가


def test_analysis_schema_requires_new_fields():
    """structured outputs 스키마에는 기본값 없이 새 필드가 들어간다."""
    schema = AnalysisOutput.model_json_schema()
    assert {"ticket_symptom", "symptom_check", "confident", "blocker"} <= set(schema["required"])


# ---------------- 화면 ----------------

def test_register_rejects_short_body(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    with pytest.raises(ValueError, match="본문이 너무 짧음"):
        ui.register_ticket("defect", "test", "집가고싶다")
    assert ui.register_ticket("defect", S1.title, S1.body).ticket_id.startswith("SR-")


def test_run_summary_shows_intake_segment():
    events = [{"ts": "2026-10-22T10:00:00", "step": "intake", "kind": "llm"},
              {"ts": "2026-10-22T10:00:05", "step": "intake", "kind": "intake", "accepted": False,
               "problems": ["시스템 수정 요청으로 볼 수 없음"]},
              {"ts": "2026-10-22T10:00:05", "step": "workflow", "kind": "end", "status": "needs_info", "tokens": 9}]
    seg = ui.run_summary(events)["segments"][0]
    assert seg["stage"] == "접수" and seg["turns"] == 1 and seg["attempt"] is None
    assert seg["result"] == "보완 요청: 시스템 수정 요청으로 볼 수 없음"
    assert ui.event_message(events[1]).startswith("[접수 검사] 보완 요청")
    assert ui.STATUS_LABELS["needs_info"] == "보완 요청"
