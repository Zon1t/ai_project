# -*- coding: utf-8 -*-
from datetime import date

import pytest

from order_settlement import db, inventory

D = date


def stock(on_hand, reserved):
    return {"sku": "SKU-1", "on_hand": on_hand, "reserved": reserved, "safety_stock": 0}


@pytest.mark.parametrize("on_hand,reserved,expected", [(10, 3, 7), (10, 0, 10), (3, 3, 0), (3, 5, 0), (4, 3, 1)])
def test_calc_available(on_hand, reserved, expected):
    assert inventory.calc_available(on_hand, reserved) == expected


@pytest.mark.parametrize("on_hand,reserved,qty,expected", [
    (10, 3, 1, True), (10, 3, 7, True), (10, 3, 8, False), (3, 5, 1, False), (3, 3, 1, False),
])
def test_can_fulfill(on_hand, reserved, qty, expected):
    assert inventory.can_fulfill(on_hand, reserved, qty) is expected


@pytest.mark.parametrize("qty", [0, -1])
def test_can_fulfill_invalid_qty(qty):
    with pytest.raises(ValueError):
        inventory.can_fulfill(10, 0, qty)


def test_reserve():
    original = stock(10, 3)
    result = inventory.reserve(original, 7)
    assert result == {"sku": "SKU-1", "on_hand": 10, "reserved": 10, "safety_stock": 0}
    assert original["reserved"] == 3
    assert inventory.reserve(stock(10, 3), 1)["reserved"] == 4


@pytest.mark.parametrize("qty", [8, 0, -1])
def test_reserve_rejected(qty):
    with pytest.raises(ValueError):
        inventory.reserve(stock(10, 3), qty)


@pytest.mark.parametrize("qty,expected", [(5, 0), (2, 3), (1, 4)])
def test_release_reservation(qty, expected):
    original = stock(10, 5)
    result = inventory.release_reservation(original, qty)
    assert result["reserved"] == expected
    assert result["on_hand"] == 10
    assert original["reserved"] == 5


@pytest.mark.parametrize("qty", [6, 0, -1])
def test_release_reservation_rejected(qty):
    with pytest.raises(ValueError):
        inventory.release_reservation(stock(10, 5), qty)


@pytest.mark.parametrize("on_hand,reserved,qty,exp_on_hand,exp_reserved", [
    (10, 5, 5, 5, 0), (10, 5, 2, 8, 3), (3, 5, 3, 0, 2), (10, 5, 1, 9, 4),
])
def test_ship_reserved(on_hand, reserved, qty, exp_on_hand, exp_reserved):
    original = stock(on_hand, reserved)
    result = inventory.ship_reserved(original, qty)
    assert result["on_hand"] == exp_on_hand
    assert result["reserved"] == exp_reserved
    assert original["on_hand"] == on_hand
    assert original["reserved"] == reserved


@pytest.mark.parametrize("on_hand,reserved,qty", [(10, 5, 6), (3, 5, 4), (10, 5, 0), (10, 5, -1)])
def test_ship_reserved_rejected(on_hand, reserved, qty):
    with pytest.raises(ValueError):
        inventory.ship_reserved(stock(on_hand, reserved), qty)


@pytest.mark.parametrize("available,safety,expected", [
    (-1, 10, "OUT_OF_STOCK"), (0, 10, "OUT_OF_STOCK"), (1, 10, "CRITICAL"), (4, 10, "CRITICAL"),
    (5, 10, "LOW"), (9, 10, "LOW"), (10, 10, "OK"), (11, 10, "OK"),
    (4, 11, "CRITICAL"), (5, 11, "LOW"), (1, 0, "OK"), (1, 3, "LOW"),
])
def test_check_safety_stock(available, safety, expected):
    assert inventory.check_safety_stock(available, safety) == expected


@pytest.mark.parametrize("available,safety,usage,lead,expected", [
    (36, 20, 5, 3, 0),
    (35, 20, 5, 3, 70),
    (34, 20, 5, 3, 80),
    (25, 20, 5, 3, 80),
    (24, 20, 5, 3, 90),
    (0, 20, 5, 3, 110),
    (11, 10, 0, 5, 0),
    (10, 10, 0, 5, 0),
    (9, 10, 0, 5, 10),
    (0, 0, 2, 4, 40),
])
def test_calc_reorder_qty(available, safety, usage, lead, expected):
    assert inventory.calc_reorder_qty(available, safety, usage, lead) == expected


@pytest.mark.parametrize("lot_no,expected", [
    ("L20240603-001", True), ("L20241231-999", True), ("L20240101-010", True),
    ("L20241203-001", True), ("L20240131-001", True),
    ("X20240603-001", False), ("L20240603_001", False), ("L20240603-000", False),
    ("L2024060-0001", False), ("L20240603-01", False), ("L20240603-0011", False),
    ("L2024A603-001", False), ("L20240603-0a1", False),
    ("L20240003-001", False), ("L20241303-001", False), ("L20240600-001", False),
    ("L20240632-001", False), ("", False),
])
def test_validate_lot_no(lot_no, expected):
    assert inventory.validate_lot_no(lot_no) is expected


def test_receive_lot():
    lots = []
    result = inventory.receive_lot(lots, "L20240603-001", 10, D(2024, 6, 3))
    assert result == [{"lot_no": "L20240603-001", "qty": 10, "received_date": D(2024, 6, 3)}]
    assert lots == []
    result2 = inventory.receive_lot(result, "L20240604-001", 1, D(2024, 6, 4))
    assert [x["lot_no"] for x in result2] == ["L20240603-001", "L20240604-001"]
    assert len(result) == 1


@pytest.mark.parametrize("lot_no,qty", [
    ("L20240604-001", 0), ("L20240604-001", -5), ("BAD", 10), ("L20240603-001", 10),
])
def test_receive_lot_rejected(lot_no, qty):
    lots = [{"lot_no": "L20240603-001", "qty": 10, "received_date": D(2024, 6, 3)}]
    with pytest.raises(ValueError):
        inventory.receive_lot(lots, lot_no, qty, D(2024, 6, 4))


LOTS = [
    {"lot_no": "L20240601-002", "qty": 3, "received_date": D(2024, 6, 1)},
    {"lot_no": "L20240603-001", "qty": 10, "received_date": D(2024, 6, 3)},
    {"lot_no": "L20240602-001", "qty": 0, "received_date": D(2024, 6, 2)},
    {"lot_no": "L20240601-001", "qty": 5, "received_date": D(2024, 6, 1)},
]


@pytest.mark.parametrize("qty,expected", [
    (1, [("L20240601-001", 1)]),
    (4, [("L20240601-001", 4)]),
    (5, [("L20240601-001", 5)]),
    (6, [("L20240601-001", 5), ("L20240601-002", 1)]),
    (8, [("L20240601-001", 5), ("L20240601-002", 3)]),
    (9, [("L20240601-001", 5), ("L20240601-002", 3), ("L20240603-001", 1)]),
    (18, [("L20240601-001", 5), ("L20240601-002", 3), ("L20240603-001", 10)]),
])
def test_allocate_fifo(qty, expected):
    assert inventory.allocate_fifo(LOTS, qty) == expected


@pytest.mark.parametrize("qty", [19, 0, -1])
def test_allocate_fifo_rejected(qty):
    with pytest.raises(ValueError):
        inventory.allocate_fifo(LOTS, qty)


def test_allocate_fifo_date_before_lot_no():
    lots = [
        {"lot_no": "L20240601-001", "qty": 5, "received_date": D(2024, 6, 2)},
        {"lot_no": "L20240602-001", "qty": 5, "received_date": D(2024, 6, 1)},
    ]
    assert inventory.allocate_fifo(lots, 6) == [("L20240602-001", 5), ("L20240601-001", 1)]


@pytest.mark.parametrize("cogs,opening,closing,expected", [
    (1200, 500, 700, 200), (1000, 600, 601, 166), (100, 1, 2, 10000),
    (1000, 0, 0, 0), (100, 1, 0, 0), (100, -10, 5, 0), (0, 500, 500, 0),
])
def test_calc_turnover_ratio(cogs, opening, closing, expected):
    assert inventory.calc_turnover_ratio(cogs, opening, closing) == expected


@pytest.mark.parametrize("available,usage,expected", [
    (100, 7, 14), (6, 7, 0), (7, 7, 1), (100, 1, 100), (100, 0, None), (100, -1, None),
])
def test_calc_days_of_supply(available, usage, expected):
    assert inventory.calc_days_of_supply(available, usage) == expected


@pytest.mark.parametrize("available,usage,expected", [
    (100, 7, D(2024, 6, 15)), (5, 7, D(2024, 6, 1)), (7, 7, D(2024, 6, 2)), (100, 0, None),
])
def test_estimate_stockout_date(available, usage, expected):
    assert inventory.estimate_stockout_date(D(2024, 6, 1), available, usage) == expected


@pytest.mark.parametrize("system,counted,expected", [
    (100, 98, {"diff": -2, "rate": 20}),
    (100, 103, {"diff": 3, "rate": 30}),
    (1000, 999, {"diff": -1, "rate": 1}),
    (3, 1, {"diff": -2, "rate": 666}),
    (1, 1, {"diff": 0, "rate": 0}),
    (0, 0, {"diff": 0, "rate": 0}),
    (0, 5, {"diff": 5, "rate": 1000}),
])
def test_calc_count_variance(system, counted, expected):
    assert inventory.calc_count_variance(system, counted) == expected


@pytest.mark.parametrize("system,counted,expected", [
    (100, 100, "MATCH"), (0, 0, "MATCH"), (1000, 999, "MINOR"),
    (1000, 980, "MINOR"), (1000, 979, "MAJOR"), (1000, 1020, "MINOR"), (1000, 1021, "MAJOR"),
    (0, 1, "MAJOR"), (100, 50, "MAJOR"),
])
def test_classify_count_variance(system, counted, expected):
    assert inventory.classify_count_variance(system, counted) == expected


@pytest.mark.parametrize("values,expected", [
    ({"c": 15, "a": 50, "d": 5, "b": 30}, {"a": "A", "b": "A", "c": "B", "d": "C"}),
    ({"a": 50, "b": 31, "c": 14, "d": 5}, {"a": "A", "b": "B", "c": "B", "d": "C"}),
    ({"a": 95, "b": 5}, {"a": "B", "b": "C"}),
    ({"a": 96, "b": 4}, {"a": "C", "b": "C"}),
    ({"a": 79, "b": 21}, {"a": "A", "b": "C"}),
    ({"y": 10, "x": 10}, {"x": "A", "y": "C"}),
    ({}, {}),
])
def test_classify_abc(values, expected):
    assert inventory.classify_abc(values) == expected


@pytest.fixture
def iconn(conn):
    inventory.ensure_inventory_schema(conn)
    return conn


def get_row(conn, sku):
    return db.query_one(conn, "SELECT * FROM stock WHERE sku = ?", (sku,))


def test_ensure_inventory_schema_idempotent(iconn):
    inventory.ensure_inventory_schema(iconn)
    assert db.query_all(iconn, "SELECT * FROM stock") == []


def test_upsert_stock_insert_and_update(iconn):
    inventory.upsert_stock(iconn, "A", 20, 5)
    assert get_row(iconn, "A") == {"sku": "A", "on_hand": 20, "reserved": 0, "safety_stock": 5}
    inventory.reserve_stock(iconn, "A", 4)
    inventory.upsert_stock(iconn, "A", 30, 8)
    assert get_row(iconn, "A") == {"sku": "A", "on_hand": 30, "reserved": 4, "safety_stock": 8}
    inventory.upsert_stock(iconn, "B", 0, 0)
    assert get_row(iconn, "B") == {"sku": "B", "on_hand": 0, "reserved": 0, "safety_stock": 0}
    assert get_row(iconn, "A")["on_hand"] == 30


@pytest.mark.parametrize("on_hand,safety", [(-1, 0), (0, -1)])
def test_upsert_stock_negative(iconn, on_hand, safety):
    with pytest.raises(ValueError):
        inventory.upsert_stock(iconn, "A", on_hand, safety)
    assert get_row(iconn, "A") is None


def test_reserve_stock(iconn):
    inventory.upsert_stock(iconn, "A", 10, 0)
    inventory.upsert_stock(iconn, "B", 10, 0)
    assert inventory.reserve_stock(iconn, "A", 3) == 7
    assert inventory.reserve_stock(iconn, "A", 7) == 0
    assert get_row(iconn, "A")["reserved"] == 10
    assert get_row(iconn, "A")["on_hand"] == 10
    assert get_row(iconn, "B")["reserved"] == 0


def test_reserve_stock_rejected(iconn):
    inventory.upsert_stock(iconn, "A", 5, 0)
    with pytest.raises(ValueError):
        inventory.reserve_stock(iconn, "A", 6)
    assert get_row(iconn, "A")["reserved"] == 0
    with pytest.raises(ValueError):
        inventory.reserve_stock(iconn, "NONE", 1)


def test_get_low_stock_skus(iconn):
    inventory.upsert_stock(iconn, "E", 10, 10)   # OK (경계)
    inventory.upsert_stock(iconn, "D", 20, 10)   # 예약 후 LOW
    inventory.upsert_stock(iconn, "C", 0, 10)    # OUT_OF_STOCK
    inventory.upsert_stock(iconn, "A", 20, 10)   # OK
    inventory.upsert_stock(iconn, "B", 9, 10)    # LOW
    inventory.reserve_stock(iconn, "D", 15)
    assert inventory.get_low_stock_skus(iconn) == ["B", "C", "D"]


def test_get_low_stock_skus_empty(iconn):
    assert inventory.get_low_stock_skus(iconn) == []
