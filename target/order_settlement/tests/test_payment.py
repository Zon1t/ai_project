# -*- coding: utf-8 -*-
from datetime import datetime

import pytest

from order_settlement import payment

CARD = "4111-1111-1111-1111"
PAID_AT = datetime(2024, 3, 1, 12, 0, 0)


@pytest.mark.parametrize("number,ok", [
    (CARD, True), ("4111111111111112", False), ("abcd-efgh", False), ("4111", False), ("5500 0000 0000 0004", True),
])
def test_validate_card_number(number, ok):
    assert payment.validate_card_number(number) is ok


def test_mask_card_number():
    assert payment.mask_card_number("4111111111111111") == "4111********1111"


def test_calc_pg_fee():
    assert payment.calc_pg_fee(100000, "CARD") == 2500
    assert payment.calc_pg_fee(100000, "BANK") == 1000
    assert payment.calc_pg_fee(100000, "POINT") == 0
    with pytest.raises(ValueError):
        payment.calc_pg_fee(100000, "CRYPTO")


def test_process_payment(conn):
    pid = payment.process_payment(conn, 1, 100000, "CARD", CARD, now=PAID_AT)
    p = payment.get_payment(conn, pid)
    assert (p["fee"], p["status"], p["paid_at"]) == (2500, "PAID", "2024-03-01 12:00:00")
    assert payment.get_payment_by_order(conn, 1)["id"] == pid


def test_process_payment_bank_without_card(conn):
    pid = payment.process_payment(conn, 1, 50000, "BANK", now=PAID_AT)
    assert payment.get_payment(conn, pid)["fee"] == 500


def test_process_payment_invalid_card(conn):
    with pytest.raises(ValueError):
        payment.process_payment(conn, 1, 100000, "CARD", "4111111111111112", now=PAID_AT)
    with pytest.raises(ValueError):
        payment.process_payment(conn, 1, 100000, "CARD", None, now=PAID_AT)


@pytest.mark.parametrize("days,fee", [(0, 0), (7, 0), (8, 5000), (30, 5000), (31, 10000)])
def test_calc_refund_fee(days, fee):
    assert payment.calc_refund_fee(100000, days) == fee


def test_refund_within_week(conn):
    pid = payment.process_payment(conn, 1, 100000, "CARD", CARD, now=PAID_AT)
    assert payment.refund_payment(conn, pid, now=datetime(2024, 3, 5)) == 100000
    p = payment.get_payment(conn, pid)
    assert (p["status"], p["refunded"]) == ("REFUNDED", 100000)
    with pytest.raises(ValueError):
        payment.refund_payment(conn, pid, now=datetime(2024, 3, 6))


def test_refund_after_ten_days(conn):
    pid = payment.process_payment(conn, 1, 100000, "CARD", CARD, now=PAID_AT)
    assert payment.refund_payment(conn, pid, now=datetime(2024, 3, 11, 12, 0, 0)) == 95000


def test_refund_missing(conn):
    with pytest.raises(ValueError):
        payment.refund_payment(conn, 99)


def test_partial_refund(conn):
    pid = payment.process_payment(conn, 1, 100000, "CARD", CARD, now=PAID_AT)
    assert payment.partial_refund(conn, pid, 30000) == 70000
    assert payment.get_payment(conn, pid)["status"] == "PARTIAL_REFUNDED"
    with pytest.raises(ValueError):
        payment.partial_refund(conn, pid, 70001)
    with pytest.raises(ValueError):
        payment.partial_refund(conn, pid, 0)
    assert payment.partial_refund(conn, pid, 70000) == 0
    assert payment.get_payment(conn, pid)["status"] == "REFUNDED"
