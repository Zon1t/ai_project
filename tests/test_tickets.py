"""티켓 생성 (F2-3): 본문 검사, 재시도, 결과 저장, 응답 상태 처리. LLM은 가짜로 대체한다."""

import json
from types import SimpleNamespace

import pytest

from sr_agent import config, llm
from sr_agent.datagen import tickets
from sr_agent.datagen.tickets import TicketDraft, TicketPair, build_input, find_leaks, generate, write_tickets

MUTANT = {
    "mutant_id": "MUT-00000001",
    "method": "discount.apply_coupon",
    "n_failed": 2,
    "traces": [{"test": "tests/test_discount.py::test_x", "trace": (
        "def test_x():\n>       assert discount.apply_coupon(100000, c) == 90000\nE       assert 80000 == 90000\n\n"
        "tests\\test_discount.py:10: AssertionError"
    )}],
}
USAGE = {"input_tokens": 500, "cached_tokens": 0, "output_tokens": 150, "reasoning_tokens": 20, "total_tokens": 650}


def _pair(body_a: str = "쿠폰을 쓰면 금액이 이상합니다.",
          body_b: str = "10만 원 주문에 쿠폰 적용 시 8만 원이 됩니다.") -> TicketPair:
    return TicketPair(ticket_a=TicketDraft(title="쿠폰 금액 오류", body=body_a),
                      ticket_b=TicketDraft(title="쿠폰 적용 결과 불일치", body=body_b))


@pytest.fixture
def fake_llm(monkeypatch):
    """parse_structured를 대체한다. responses에 넣은 순서대로 돌려주고, 받은 입력을 기록한다."""
    calls = []
    responses = []

    def fake(client, **kwargs):
        calls.append(kwargs)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return llm.ParsedResponse(item, USAGE, 0.1, "resp_1")

    monkeypatch.setattr(llm, "parse_structured", fake)
    return SimpleNamespace(calls=calls, responses=responses)


@pytest.mark.parametrize(("text", "expected"), [
    ("쿠폰을 두 장 쓰면 금액이 음수입니다.", []),
    ("apply_coupon 결과가 이상합니다.", ["apply_coupon"]),
    ("Apply_Coupon 확인 필요", ["Apply_Coupon", "apply_coupon"]),
    ("discount.apply_coupon 호출 시", ["apply_coupon", "discount.apply_coupon"]),
    ("current_amount 값이 비어 있음", ["current_amount"]),
    ("2024-03-08 정산, VIP 등급", []),
])
def test_find_leaks(text, expected):
    assert find_leaks(text, "discount.apply_coupon") == expected


def test_single_word_function_name_is_leak():
    assert find_leaks("DB connect 실패", "db.connect") == ["connect"]
    assert find_leaks("연결 실패", "db.connect") == []


def test_build_input_has_business_area_and_failure_but_no_code_diff():
    text = build_input(MUTANT)
    assert text.startswith("업무 영역: 할인 계산")
    assert "실패한 자동 테스트 수: 2" in text
    assert "assert 80000 == 90000" in text
    assert "diff" not in text


def test_instructions_are_fixed_text():
    """prompt caching: 지시문에 실행마다 바뀌는 값을 넣지 않는다."""
    assert "{" not in tickets.INSTRUCTIONS and config.MODEL_BULK not in tickets.INSTRUCTIONS


def test_write_tickets_ok(fake_llm, tmp_path):
    fake_llm.responses.append(_pair())
    log = tickets.RunLog("t1", tmp_path)
    result = write_tickets(None, MUTANT, log)
    assert result["status"] == "ok" and result["attempts"] == 1
    assert result["ticket_a"]["title"] == "쿠폰 금액 오류"
    assert result["usage"]["input_tokens"] == 500
    call = fake_llm.calls[0]
    assert call["model"] == config.MODEL_BULK and call["effort"] == config.TICKETGEN_REASONING_EFFORT
    logged = json.loads((tmp_path / "t1.jsonl").read_text(encoding="utf-8"))
    assert logged["tokens"] == USAGE and logged["mutant_id"] == MUTANT["mutant_id"]


def test_write_tickets_retries_with_feedback_then_rejects(fake_llm):
    fake_llm.responses += [_pair(body_a="apply_coupon 오류"), _pair()]
    result = write_tickets(None, MUTANT)
    assert result["status"] == "ok" and result["attempts"] == 2
    assert result["usage"]["input_tokens"] == 1000  # 두 번 호출한 합
    retry_input = fake_llm.calls[1]["input"]
    assert retry_input.startswith(fake_llm.calls[0]["input"])  # 앞부분 동일 → 캐시 유지
    assert "apply_coupon" in retry_input.split("[이전 응답의 문제]")[1]

    fake_llm.responses += [_pair(body_a="apply_coupon 오류")] * config.TICKETGEN_MAX_ATTEMPTS
    result = write_tickets(None, MUTANT)
    assert result["status"] == "rejected" and result["leaks"] == ["apply_coupon"]


@pytest.mark.parametrize(("error", "status"), [
    (llm.LLMRefusal("거부"), "refused"),
    (llm.LLMError("incomplete"), "error"),
])
def test_write_tickets_refusal_and_error(fake_llm, error, status):
    fake_llm.responses.append(error)
    assert write_tickets(None, MUTANT)["status"] == status


def test_generate_resumes_and_limits(fake_llm, tmp_path):
    mutants_path = tmp_path / "mutants.jsonl"
    others = [{**MUTANT, "mutant_id": f"MUT-0000000{i}"} for i in range(2, 5)]
    mutants_path.write_text("\n".join(json.dumps(m) for m in [MUTANT, *others]) + "\n", encoding="utf-8")
    drafts = tmp_path / "drafts.jsonl"
    drafts.write_text(json.dumps({"mutant_id": MUTANT["mutant_id"], "status": "ok"}) + "\n", encoding="utf-8")
    fake_llm.responses += [_pair()] * 3

    summary = generate(client=object(), mutants_path=mutants_path, drafts_path=drafts, limit=2,
                       workers=1, runs_dir=tmp_path)
    assert summary["already_done"] == 1 and summary["requested"] == 2 and summary["status"] == {"ok": 2}
    summary = generate(client=object(), mutants_path=mutants_path, drafts_path=drafts, workers=1, runs_dir=tmp_path)
    assert summary["requested"] == 1  # 남은 1개만
    lines = drafts.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4 and len({json.loads(line)["mutant_id"] for line in lines}) == 4


def _response(status="completed", parsed=None, refusal=None):
    content = [SimpleNamespace(type="refusal", refusal=refusal)] if refusal else [SimpleNamespace(type="output_text")]
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15,
                            input_tokens_details=SimpleNamespace(cached_tokens=4),
                            output_tokens_details=SimpleNamespace(reasoning_tokens=2))
    return SimpleNamespace(id="r1", status=status, incomplete_details={"reason": "max_output_tokens"},
                           output=[SimpleNamespace(type="message", content=content)], output_parsed=parsed,
                           usage=usage)


def _client(response):
    sent = {}

    def parse(**kwargs):
        sent.update(kwargs)
        return response
    return SimpleNamespace(responses=SimpleNamespace(parse=parse)), sent


def test_parse_structured_checks_status_and_refusal():
    client, sent = _client(_response(parsed=_pair()))
    res = llm.parse_structured(client, model="m", instructions="i", input="x", text_format=TicketPair, effort="low")
    assert res.parsed == _pair()
    assert res.usage == {"input_tokens": 10, "cached_tokens": 4, "output_tokens": 5, "reasoning_tokens": 2,
                         "total_tokens": 15}
    assert sent["reasoning"] == {"effort": "low"} and sent["max_output_tokens"] == config.MAX_OUTPUT_TOKENS
    assert "temperature" not in sent

    client, _ = _client(_response(status="incomplete"))
    with pytest.raises(llm.LLMError, match="incomplete"):
        llm.parse_structured(client, model="m", instructions="i", input="x", text_format=TicketPair, effort="low")
    client, _ = _client(_response(refusal="할 수 없습니다"))
    with pytest.raises(llm.LLMRefusal):
        llm.parse_structured(client, model="m", instructions="i", input="x", text_format=TicketPair, effort="low")
