# -*- coding: utf-8 -*-
import pytest

from order_settlement import notification as nt


def test_find_template_vars_order_and_dedup():
    tpl = "#{name}님 주문 #{order_id} 접수. #{name}님 감사합니다. #{amount}원"
    assert nt.find_template_vars(tpl) == ["name", "order_id", "amount"]
    assert nt.find_template_vars("변수 없음 {name} #name") == []


def test_find_missing_vars():
    tpl = "#{a} #{b} #{c}"
    assert nt.find_missing_vars(tpl, {"a": 1, "b": None}) == ["b", "c"]
    assert nt.find_missing_vars(tpl, {"a": 0, "b": "", "c": False}) == []
    assert nt.find_missing_vars("고정 문구", {}) == []


def test_render_template():
    tpl = "#{name}님 #{amount}원 결제 완료 (#{name})"
    assert nt.render_template(tpl, {"name": "홍길동", "amount": 15000, "extra": "x"}) == \
        "홍길동님 15000원 결제 완료 (홍길동)"
    assert nt.render_template("고정", {}) == "고정"


def test_render_template_missing_raises():
    with pytest.raises(ValueError) as e:
        nt.render_template("#{a} #{b} #{c}", {"b": 1})
    assert "a, c" in str(e.value)
    with pytest.raises(ValueError):
        nt.render_template("#{a}", {"a": None})


@pytest.mark.parametrize("text,expected", [
    ("", 0),
    ("abc", 3),
    ("가나", 4),
    ("a가1", 4),
    (chr(127), 1),
    (chr(128), 2),
])
def test_calc_message_bytes(text, expected):
    assert nt.calc_message_bytes(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("가" * 45, "SMS"),
    ("가" * 44 + "a", "SMS"),
    ("가" * 44 + "ab", "SMS"),
    ("가" * 45 + "a", "LMS"),
    ("a" * 90, "SMS"),
    ("a" * 91, "LMS"),
    ("가" * 1000, "LMS"),
    ("a" * 2000, "LMS"),
])
def test_classify_message_type(text, expected):
    assert nt.classify_message_type(text) == expected


def test_classify_message_type_too_long():
    with pytest.raises(ValueError):
        nt.classify_message_type("a" * 2001)
    with pytest.raises(ValueError):
        nt.classify_message_type("가" * 1000 + "a")


@pytest.mark.parametrize("hour,expected", [
    (0, True), (1, True), (6, True), (7, True), (8, False), (9, False),
    (12, False), (19, False), (20, False), (21, True), (22, True), (23, True),
])
def test_is_night_time(hour, expected):
    assert nt.is_night_time(hour) is expected


@pytest.mark.parametrize("hour", [-1, 24, 25])
def test_is_night_time_invalid(hour):
    with pytest.raises(ValueError):
        nt.is_night_time(hour)


@pytest.mark.parametrize("category,hour,expected", [
    ("AD", 20, True), ("AD", 21, False), ("AD", 7, False), ("AD", 8, True), ("AD", 0, False),
    ("INFO", 21, True), ("INFO", 7, True), ("INFO", 23, True), ("INFO", 12, True),
])
def test_can_send_at(category, hour, expected):
    assert nt.can_send_at(category, hour) is expected


@pytest.mark.parametrize("category,hour,expected", [
    ("AD", 8, 0), ("AD", 20, 0), ("AD", 21, 11), ("AD", 22, 10), ("AD", 23, 9),
    ("AD", 0, 8), ("AD", 1, 7), ("AD", 7, 1), ("INFO", 23, 0), ("INFO", 3, 0),
])
def test_hours_until_allowed(category, hour, expected):
    assert nt.hours_until_allowed(category, hour) == expected


@pytest.mark.parametrize("text,category,expected", [
    ("세일 안내", "AD", "(광고) 세일 안내"),
    ("(광고) 세일 안내", "AD", "(광고) 세일 안내"),
    ("주문 접수", "INFO", "주문 접수"),
    ("세일 (광고)", "AD", "(광고) 세일 (광고)"),
])
def test_add_ad_prefix(text, category, expected):
    assert nt.add_ad_prefix(text, category) == expected


ALL = {"APP_PUSH": True, "ALIMTALK": True, "SMS": True}


@pytest.mark.parametrize("consents,app,category,expected", [
    (ALL, True, "INFO", "APP_PUSH"),
    (ALL, False, "INFO", "ALIMTALK"),
    (ALL, False, "AD", "SMS"),
    (ALL, True, "AD", "APP_PUSH"),
    ({"APP_PUSH": False, "ALIMTALK": True, "SMS": True}, True, "INFO", "ALIMTALK"),
    ({"APP_PUSH": False, "ALIMTALK": False, "SMS": True}, True, "INFO", "SMS"),
    ({"APP_PUSH": True, "SMS": True}, True, "INFO", "APP_PUSH"),
    ({"SMS": True}, True, "INFO", "SMS"),
    ({"ALIMTALK": True}, True, "AD", None),
    ({"APP_PUSH": True}, False, "INFO", None),
    ({}, True, "INFO", None),
    ({"APP_PUSH": False, "ALIMTALK": False, "SMS": False}, True, "INFO", None),
])
def test_select_channel(consents, app, category, expected):
    assert nt.select_channel(consents, app, category) == expected


def test_select_channel_default_category_is_info():
    assert nt.select_channel({"ALIMTALK": True}, False) == "ALIMTALK"


@pytest.mark.parametrize("attempt,expected", [
    (1, 30), (2, 60), (3, 120), (4, 240), (5, 480), (6, 600), (10, 600),
])
def test_calc_retry_delay(attempt, expected):
    assert nt.calc_retry_delay(attempt) == expected


@pytest.mark.parametrize("attempt", [0, -1])
def test_calc_retry_delay_invalid(attempt):
    with pytest.raises(ValueError):
        nt.calc_retry_delay(attempt)


@pytest.mark.parametrize("code,expected", [
    ("TIMEOUT", True), ("RATE_LIMITED", True), ("SERVER_ERROR", True),
    ("INVALID_NUMBER", False), ("BLOCKED", False), ("", False),
])
def test_is_retryable(code, expected):
    assert nt.is_retryable(code) is expected


@pytest.mark.parametrize("code,attempt,expected", [
    ("TIMEOUT", 0, True), ("TIMEOUT", 2, True), ("TIMEOUT", 3, False), ("TIMEOUT", 4, False),
    ("SERVER_ERROR", 1, True), ("INVALID_NUMBER", 0, False), ("BLOCKED", 2, False),
])
def test_should_retry(code, attempt, expected):
    assert nt.should_retry(code, attempt) is expected


@pytest.mark.parametrize("sent,requested,category,expected", [
    (4, 1, "AD", True), (5, 1, "AD", False), (0, 5, "AD", True), (0, 6, "AD", False),
    (3, 2, "AD", True), (3, 3, "AD", False), (6, 0, "AD", False), (5, 0, "AD", True),
    (100, 1, "INFO", True), (5, 1, "INFO", True),
])
def test_check_daily_limit(sent, requested, category, expected):
    assert nt.check_daily_limit(sent, requested, category) is expected


def test_check_daily_limit_defaults():
    assert nt.check_daily_limit(100) is True
    assert nt.check_daily_limit(4, category="AD") is True
    assert nt.check_daily_limit(5, category="AD") is False


@pytest.mark.parametrize("channel,text,count,expected", [
    ("SMS", "짧은 문자", 1, 20),
    ("SMS", "가" * 46, 1, 50),
    ("SMS", "가" * 45, 3, 60),
    ("LMS", "짧음", 2, 100),
    ("ALIMTALK", "가" * 100, 10, 80),
    ("APP_PUSH", "x", 5000, 0),
    ("ALIMTALK", "x", 999, 7992),
    ("ALIMTALK", "x", 1000, 7200),
    ("SMS", "x", 1001, 18018),
    ("SMS", "x", 0, 0),
    ("SMS", "x", -5, 0),
    ("FAX", "x", 0, 0),
])
def test_calc_send_cost(channel, text, count, expected):
    assert nt.calc_send_cost(channel, text, count) == expected


def test_calc_send_cost_unknown_channel():
    with pytest.raises(ValueError):
        nt.calc_send_cost("FAX", "x", 1)


@pytest.mark.parametrize("phone,expected", [
    ("010-1234-5678", "01012345678"),
    ("01012345678", "01012345678"),
    (" 010 9999 0000 ", "01099990000"),
])
def test_normalize_phone(phone, expected):
    assert nt.normalize_phone(phone) == expected


@pytest.mark.parametrize("phone", [None, "", "011-1234-5678", "010-123-5678", "010-1234-56789", "02-123-4567"])
def test_normalize_phone_invalid(phone):
    with pytest.raises(ValueError):
        nt.normalize_phone(phone)


def test_summarize_results():
    assert nt.summarize_results([]) == {"total": 0, "sent": 0, "failed": 0, "success_rate": 0}
    assert nt.summarize_results(["SENT", "FAILED", "SENT"]) == \
        {"total": 3, "sent": 2, "failed": 1, "success_rate": 66}
    assert nt.summarize_results(["SENT", "PENDING"]) == \
        {"total": 2, "sent": 1, "failed": 0, "success_rate": 50}
    assert nt.summarize_results(["SENT"]) == {"total": 1, "sent": 1, "failed": 0, "success_rate": 100}
    assert nt.summarize_results(["FAILED"]) == {"total": 1, "sent": 0, "failed": 1, "success_rate": 0}


def test_ensure_notification_schema_idempotent(conn):
    nt.ensure_notification_schema(conn)
    nt.ensure_notification_schema(conn)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE name = 'notifications'").fetchall()
    assert len(rows) == 1


def test_record_notification(conn):
    nt.ensure_notification_schema(conn)
    first = nt.record_notification(conn, 1, "SMS", "AD", "SENT", "2024-03-01 10:00:00")
    second = nt.record_notification(conn, 1, "SMS", "AD", "FAILED", "2024-03-01 11:00:00")
    assert second == first + 1
    row = dict(conn.execute("SELECT * FROM notifications WHERE id = ?", (first,)).fetchone())
    assert row == {"id": first, "customer_id": 1, "channel": "SMS", "category": "AD",
                   "status": "SENT", "sent_at": "2024-03-01 10:00:00"}


def test_record_notification_invalid_status(conn):
    nt.ensure_notification_schema(conn)
    with pytest.raises(ValueError):
        nt.record_notification(conn, 1, "SMS", "AD", "PENDING", "2024-03-01 10:00:00")
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0


def test_count_sent_on(conn):
    nt.ensure_notification_schema(conn)
    nt.record_notification(conn, 1, "SMS", "AD", "SENT", "2024-03-01 00:00:00")
    nt.record_notification(conn, 1, "SMS", "AD", "SENT", "2024-03-01 23:59:59")
    nt.record_notification(conn, 1, "SMS", "AD", "FAILED", "2024-03-01 12:00:00")
    nt.record_notification(conn, 1, "SMS", "INFO", "SENT", "2024-03-01 12:00:00")
    nt.record_notification(conn, 2, "SMS", "AD", "SENT", "2024-03-01 12:00:00")
    nt.record_notification(conn, 1, "SMS", "AD", "SENT", "2024-03-02 00:00:00")
    assert nt.count_sent_on(conn, 1, "2024-03-01") == 2
    assert nt.count_sent_on(conn, 1, "2024-03-01", "INFO") == 1
    assert nt.count_sent_on(conn, 2, "2024-03-01") == 1
    assert nt.count_sent_on(conn, 1, "2024-03-02") == 1
    assert nt.count_sent_on(conn, 3, "2024-03-01") == 0
