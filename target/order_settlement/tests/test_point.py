# -*- coding: utf-8 -*-
from datetime import date

import pytest

from order_settlement import point


def lot(earned_on, amount, remaining=None):
    return {"earned_on": earned_on, "amount": amount,
            "remaining": amount if remaining is None else remaining}


@pytest.mark.parametrize("grade,expected", [("BASIC", 1), ("SILVER", 2), ("GOLD", 3), ("VIP", 5)])
def test_get_earn_rate(grade, expected):
    assert point.get_earn_rate(grade) == expected


def test_get_earn_rate_unknown():
    with pytest.raises(ValueError, match="등급"):
        point.get_earn_rate("PLATINUM")


@pytest.mark.parametrize("paid,grade,expected", [
    (9999, "VIP", 0),
    (10000, "BASIC", 100),
    (10001, "BASIC", 100),
    (10000, "GOLD", 300),
    (55555, "SILVER", 1111),
    (99980, "VIP", 4999),
    (100000, "VIP", 5000),
    (100020, "VIP", 5000),
    (300000, "VIP", 5000),
])
def test_calc_earn_points(paid, grade, expected):
    assert point.calc_earn_points(paid, grade) == expected


def test_calc_earn_points_unknown_grade():
    with pytest.raises(ValueError):
        point.calc_earn_points(10000, "NONE")


@pytest.mark.parametrize("paid,first,expected", [
    (10000, True, 2100),
    (10000, False, 100),
    (9999, True, 0),
    (9999, False, 0),
    (20000, False, 200),
    (300000, True, 5000),
    (800000, True, 7000),
])
def test_calc_total_earn(paid, first, expected):
    assert point.calc_total_earn(paid, "BASIC", first) == expected


@pytest.mark.parametrize("points,expected", [(0, 0), (99, 0), (100, 100), (1099, 1000), (1100, 1100)])
def test_round_use_points(points, expected):
    assert point.round_use_points(points) == expected


@pytest.mark.parametrize("paid,balance,expected", [
    (10000, 5000, 3000),
    (10000, 3000, 3000),
    (10000, 2950, 2900),
    (10050, 9999, 3000),
    (10400, 9999, 3100),
    (10000, 0, 0),
])
def test_calc_max_usable(paid, balance, expected):
    assert point.calc_max_usable(paid, balance) == expected


@pytest.mark.parametrize("points,paid,balance", [
    (1000, 10000, 5000),
    (3000, 10000, 5000),
    (2000, 100000, 2000),
])
def test_validate_use_points_ok(points, paid, balance):
    assert point.validate_use_points(points, paid, balance) is True


@pytest.mark.parametrize("points,paid,balance,msg", [
    (999, 100000, 5000, "최소"),
    (900, 100000, 5000, "최소"),
    (0, 100000, 5000, "최소"),
    (1050, 100000, 5000, "단위"),
    (1001, 100000, 5000, "단위"),
    (2000, 100000, 1900, "부족"),
    (2100, 100000, 2000, "부족"),
    (3100, 10000, 5000, "상한"),
    (4000, 10000, 5000, "상한"),
])
def test_validate_use_points_error(points, paid, balance, msg):
    with pytest.raises(ValueError, match=msg):
        point.validate_use_points(points, paid, balance)


def test_calc_expiry_date():
    assert point.calc_expiry_date(date(2024, 1, 1)) == date(2024, 12, 31)
    assert point.calc_expiry_date(date(2025, 3, 1)) == date(2026, 3, 1)


@pytest.mark.parametrize("today,expected", [
    (date(2024, 1, 1), False),
    (date(2024, 12, 30), False),
    (date(2024, 12, 31), True),
    (date(2025, 1, 1), True),
])
def test_is_expired(today, expected):
    assert point.is_expired(date(2024, 1, 1), today) is expected


@pytest.mark.parametrize("today,expected", [
    (date(2024, 12, 1), 30),
    (date(2024, 12, 31), 0),
    (date(2025, 1, 1), -1),
    (date(2024, 1, 1), 365),
])
def test_days_until_expiry(today, expected):
    assert point.days_until_expiry(date(2024, 1, 1), today) == expected


@pytest.mark.parametrize("item,today,expected", [
    (lot(date(2024, 1, 1), 1000, 0), date(2024, 6, 1), "USED"),
    (lot(date(2024, 1, 1), 1000, 0), date(2025, 6, 1), "USED"),
    (lot(date(2024, 1, 1), 1000, 500), date(2024, 12, 31), "EXPIRED"),
    (lot(date(2024, 1, 1), 1000), date(2024, 12, 31), "EXPIRED"),
    (lot(date(2024, 1, 1), 1000, 999), date(2024, 12, 30), "PARTIAL"),
    (lot(date(2024, 1, 1), 1000, 1), date(2024, 6, 1), "PARTIAL"),
    (lot(date(2024, 1, 1), 1000), date(2024, 12, 30), "ACTIVE"),
])
def test_get_lot_status(item, today, expected):
    assert point.get_lot_status(item, today) == expected


def test_calc_available_balance():
    lots = [
        lot(date(2024, 1, 1), 1000, 300),     # 2024-12-31 만료
        lot(date(2024, 1, 2), 2000, 2000),    # 2025-01-01 만료
        lot(date(2024, 6, 1), 500, 0),
        lot(date(2024, 7, 1), 700, 700),
    ]
    assert point.calc_available_balance(lots, date(2024, 12, 30)) == 3000
    assert point.calc_available_balance(lots, date(2024, 12, 31)) == 2700
    assert point.calc_available_balance(lots, date(2025, 1, 1)) == 700
    assert point.calc_available_balance([], date(2025, 1, 1)) == 0


def test_calc_expiring_points():
    lots = [
        lot(date(2024, 1, 1), 1000, 400),     # 만료일 2024-12-31
        lot(date(2024, 1, 31), 2000, 2000),   # 만료일 2025-01-30
        lot(date(2024, 2, 1), 3000, 3000),    # 만료일 2025-01-31
        lot(date(2024, 6, 1), 800, 800),      # 만료일 2025-06-01
    ]
    today = date(2024, 12, 31)                # 첫 건은 오늘 만료
    assert point.calc_expiring_points(lots, today) == 2000
    assert point.calc_expiring_points(lots, today, 31) == 5000
    assert point.calc_expiring_points(lots, today, 29) == 0
    assert point.calc_expiring_points(lots, date(2024, 12, 30), 1) == 400
    assert point.calc_expiring_points(lots, date(2024, 12, 30), 0) == 0


def test_allocate_use_fifo_oldest_first():
    lots = [
        lot(date(2024, 6, 1), 1000),
        lot(date(2024, 3, 1), 500, 200),
        lot(date(2023, 1, 1), 900),           # 만료
        lot(date(2024, 4, 1), 300, 0),        # 소진
        lot(date(2024, 5, 1), 400),
    ]
    today = date(2024, 7, 1)
    assert point.allocate_use_fifo(lots, 500, today) == [(1, 200), (4, 300)]
    assert point.allocate_use_fifo(lots, 600, today) == [(1, 200), (4, 400)]
    assert point.allocate_use_fifo(lots, 700, today) == [(1, 200), (4, 400), (0, 100)]
    assert point.allocate_use_fifo(lots, 1600, today) == [(1, 200), (4, 400), (0, 1000)]
    assert point.allocate_use_fifo(lots, 100, today) == [(1, 100)]
    assert point.allocate_use_fifo(lots, 0, today) == []


def test_allocate_use_fifo_small_remaining():
    lots = [lot(date(2024, 2, 1), 500), lot(date(2024, 1, 10), 100, 1)]
    today = date(2024, 7, 1)
    assert point.allocate_use_fifo(lots, 3, today) == [(1, 1), (0, 2)]
    assert point.allocate_use_fifo(lots, 1, today) == [(1, 1)]
    assert point.allocate_use_fifo([lot(date(2024, 1, 10), 500, 200), lot(date(2024, 2, 1), 500)],
                                   201, today) == [(0, 200), (1, 1)]


def test_allocate_use_fifo_insufficient():
    lots = [lot(date(2024, 6, 1), 1000), lot(date(2023, 1, 1), 900)]
    with pytest.raises(ValueError, match="1 부족"):
        point.allocate_use_fifo(lots, 1001, date(2024, 7, 1))
    with pytest.raises(ValueError, match="500 부족"):
        point.allocate_use_fifo(lots, 1500, date(2024, 7, 1))


@pytest.mark.parametrize("earned,paid,refund,expected", [
    (1000, 50000, 50000, 1000),
    (1000, 50000, 25000, 500),
    (999, 50000, 10000, 200),
    (1, 3, 1, 1),
    (1000, 50000, 1, 1),
    (0, 50000, 25000, 0),
])
def test_calc_reclaim_points(earned, paid, refund, expected):
    assert point.calc_reclaim_points(earned, paid, refund) == expected


@pytest.mark.parametrize("refund", [0, -1, 50001])
def test_calc_reclaim_points_error(refund):
    with pytest.raises(ValueError, match="환불 금액"):
        point.calc_reclaim_points(1000, 50000, refund)


@pytest.mark.parametrize("used,paid,refund,expected", [
    (1050, 50000, 50000, 1050),
    (1000, 50000, 25000, 500),
    (1050, 50000, 25000, 500),
    (3000, 50000, 49999, 2900),
    (3000, 50000, 1, 0),
])
def test_calc_restore_points(used, paid, refund, expected):
    assert point.calc_restore_points(used, paid, refund) == expected


@pytest.mark.parametrize("refund", [0, -1, 50001])
def test_calc_restore_points_error(refund):
    with pytest.raises(ValueError, match="환불 금액"):
        point.calc_restore_points(1000, 50000, refund)


def test_apply_refund_to_balance():
    assert point.apply_refund_to_balance(3000, 1000, 2000, 50000, 25000) == {
        "reclaim": 500, "restore": 1000, "balance": 3500, "deficit": 0}
    assert point.apply_refund_to_balance(200, 1000, 0, 50000, 50000) == {
        "reclaim": 1000, "restore": 0, "balance": 0, "deficit": 800}
    assert point.apply_refund_to_balance(1000, 1000, 0, 50000, 50000) == {
        "reclaim": 1000, "restore": 0, "balance": 0, "deficit": 0}
    assert point.apply_refund_to_balance(999, 1000, 0, 50000, 50000) == {
        "reclaim": 1000, "restore": 0, "balance": 0, "deficit": 1}
    with pytest.raises(ValueError):
        point.apply_refund_to_balance(1000, 1000, 0, 50000, 0)


def test_summarize_history():
    history = [
        {"type": "EARN", "amount": 5000},
        {"type": "EARN", "amount": 1000},
        {"type": "USE", "amount": 2000},
        {"type": "EXPIRE", "amount": 300},
        {"type": "RECLAIM", "amount": 70},
        {"type": "RESTORE", "amount": 400},
    ]
    assert point.summarize_history(history) == {
        "EARN": 6000, "USE": 2000, "EXPIRE": 300, "RECLAIM": 70, "RESTORE": 400, "NET": 4030}
    assert point.summarize_history([]) == {
        "EARN": 0, "USE": 0, "EXPIRE": 0, "RECLAIM": 0, "RESTORE": 0, "NET": 0}


def test_summarize_history_unknown_type():
    with pytest.raises(ValueError, match="BONUS"):
        point.summarize_history([{"type": "BONUS", "amount": 100}])


@pytest.fixture
def pconn(conn):
    point.ensure_point_schema(conn)
    return conn


def test_ensure_point_schema_idempotent(pconn):
    point.ensure_point_schema(pconn)
    rows = pconn.execute("SELECT name FROM sqlite_master WHERE name = 'point_ledger'").fetchall()
    assert len(rows) == 1


def test_add_point_entry_and_balance(pconn):
    first = point.add_point_entry(pconn, 1, "EARN", 5000, "2024-01-01 10:00:00")
    second = point.add_point_entry(pconn, 1, "USE", 1200, "2024-01-02 10:00:00")
    point.add_point_entry(pconn, 1, "RESTORE", 1, "2024-01-03 10:00:00")
    point.add_point_entry(pconn, 2, "EARN", 9000, "2024-01-01 10:00:00")
    assert second == first + 1
    row = pconn.execute("SELECT customer_id, type, amount, created_at FROM point_ledger WHERE id = ?",
                        (first,)).fetchone()
    assert tuple(row) == (1, "EARN", 5000, "2024-01-01 10:00:00")
    assert point.get_point_balance(pconn, 1) == 3801
    assert point.get_point_balance(pconn, 2) == 9000
    assert point.get_point_balance(pconn, 3) == 0


@pytest.mark.parametrize("entry_type,amount,msg", [
    ("BONUS", 100, "유형"),
    ("EARN", 0, "금액"),
    ("EARN", -100, "금액"),
])
def test_add_point_entry_error(pconn, entry_type, amount, msg):
    with pytest.raises(ValueError, match=msg):
        point.add_point_entry(pconn, 1, entry_type, amount, "2024-01-01 10:00:00")
    assert pconn.execute("SELECT COUNT(*) FROM point_ledger").fetchone()[0] == 0
