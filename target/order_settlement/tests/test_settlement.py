# -*- coding: utf-8 -*-
from datetime import date, datetime, timedelta

import pytest

from order_settlement import coupon, order, settlement


def test_calc_commission_and_vat():
    assert settlement.calc_commission(100000) == 12000
    assert settlement.calc_commission(100000, "PARTNER") == 10000
    assert settlement.calc_vat(110000) == 10000


def test_settlement_amount_without_coupon():
    # 결제 100,000 - PG 2,500 - 수수료 12,000
    assert settlement.calc_settlement_amount(100000, "BASIC", []) == 85500


def test_settlement_amount_with_coupon():
    c = {"type": "FIXED", "value": 10000, "min_order": 100000}
    # 결제 90,000 - PG 2,250 - 수수료 10,800
    assert settlement.calc_settlement_amount(100000, "BASIC", [c]) == 76950


def test_settlement_amount_bank_partner():
    assert settlement.calc_settlement_amount(200000, "GOLD", [], "BANK", "PARTNER") == 190000 - 1900 - 19000


def test_due_date_mid_month():
    # KST 1/15 → 월말 1/31(수) → 영업일 5일째 2/7
    assert settlement.calc_due_date(datetime(2024, 1, 15, 3, 0)) == date(2024, 2, 7)


def test_due_date_kst_month_boundary():
    # UTC 1/31 16:00 = KST 2/1 01:00 → 2월 말 마감 → 3/1 공휴일 → 3/8
    assert settlement.calc_due_date(datetime(2024, 1, 31, 16, 0)) == date(2024, 3, 8)


def test_due_date_default_now():
    due = settlement.calc_due_date()
    assert isinstance(due, date)
    assert due > date.today() - timedelta(days=1)


def test_settlement_period():
    assert settlement.get_settlement_period(2024, 2) == (date(2024, 2, 1), date(2024, 2, 29))


def _paid_order(conn):
    coupon.create_coupon(conn, "SAVE10K", "FIXED", 10000)
    oid = order.create_order(conn, 1, ["SAVE10K"], created_at=datetime(2024, 3, 10, 1, 0))
    order.add_item(conn, oid, "모니터", 60000, 2)
    order.change_status(conn, oid, "PAID")
    return oid


def test_settle_order(seeded):
    sid = settlement.settle_order(seeded, _paid_order(seeded))
    s = settlement.get_settlement(seeded, sid)
    # 결제 110,000 - PG 2,750 - 수수료 13,200, 지급일: 3/31(일) 마감 → 4/5
    assert (s["amount"], s["commission"], s["due_date"], s["status"]) == (94050, 13200, "2024-04-05", "PENDING")


def test_settle_order_invalid(seeded):
    oid = order.create_order(seeded, 1, created_at=datetime(2024, 3, 10))
    with pytest.raises(ValueError):
        settlement.settle_order(seeded, oid)
    with pytest.raises(ValueError):
        settlement.settle_order(seeded, 99)


def test_close_and_list(seeded):
    sid = settlement.settle_order(seeded, _paid_order(seeded))
    assert settlement.close_settlement(seeded, sid) is True
    with pytest.raises(ValueError):
        settlement.close_settlement(seeded, sid)
    with pytest.raises(ValueError):
        settlement.close_settlement(seeded, 99)
    assert [s["id"] for s in settlement.list_settlements(seeded, "CLOSED")] == [sid]
    assert settlement.list_settlements(seeded, "PENDING") == []
    assert len(settlement.list_settlements(seeded)) == 1


def test_sum_settlements(seeded):
    settlement.settle_order(seeded, _paid_order(seeded))
    assert settlement.sum_settlements(seeded, date(2024, 4, 1), date(2024, 4, 30)) == 94050
    assert settlement.sum_settlements(seeded, date(2024, 5, 1), date(2024, 5, 31)) == 0


def test_is_settlement_overdue():
    s = {"status": "PENDING", "due_date": "2024-04-05"}
    assert settlement.is_settlement_overdue(s, date(2024, 4, 6)) is True
    assert settlement.is_settlement_overdue(s, date(2024, 4, 5)) is False
    assert settlement.is_settlement_overdue(dict(s, status="CLOSED"), date(2024, 5, 1)) is False
