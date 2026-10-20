# -*- coding: utf-8 -*-
from datetime import date, datetime

import pytest

from order_settlement import db, shipping

D = date


@pytest.mark.parametrize("region,expected", [("METRO", 3000), ("LOCAL", 3500)])
def test_get_region_base_fee(region, expected):
    assert shipping.get_region_base_fee(region) == expected


def test_get_region_base_fee_unknown():
    with pytest.raises(ValueError):
        shipping.get_region_base_fee("MARS")


@pytest.mark.parametrize("zip_code,expected", [
    ("06236", "NORMAL"), ("62999", "NORMAL"), ("63000", "JEJU"), ("63300", "JEJU"),
    ("63644", "JEJU"), ("63645", "NORMAL"),
    ("23099", "NORMAL"), ("23100", "ISLAND"), ("23116", "ISLAND"), ("23117", "NORMAL"),
    ("40199", "NORMAL"), ("40200", "ISLAND"), ("40240", "ISLAND"), ("40241", "NORMAL"),
    ("59101", "NORMAL"), ("59102", "ISLAND"), ("59166", "ISLAND"), ("59167", "NORMAL"),
])
def test_classify_remote_area(zip_code, expected):
    assert shipping.classify_remote_area(zip_code) == expected


@pytest.mark.parametrize("zip_code", ["1234", "123456", "1234a", "", "63 00"])
def test_classify_remote_area_invalid(zip_code):
    with pytest.raises(ValueError):
        shipping.classify_remote_area(zip_code)


@pytest.mark.parametrize("zip_code,expected", [("63000", 3000), ("40200", 5000), ("59166", 5000), ("06236", 0)])
def test_calc_remote_surcharge(zip_code, expected):
    assert shipping.calc_remote_surcharge(zip_code) == expected


@pytest.mark.parametrize("weight,expected", [
    (1, 0), (2000, 0), (2001, 1000), (5000, 1000), (5001, 2500),
    (10000, 2500), (10001, 4000), (19999, 4000), (20000, 4000),
])
def test_calc_weight_fee(weight, expected):
    assert shipping.calc_weight_fee(weight) == expected


@pytest.mark.parametrize("weight", [0, -1, 20001, 30000])
def test_calc_weight_fee_invalid(weight):
    with pytest.raises(ValueError):
        shipping.calc_weight_fee(weight)


@pytest.mark.parametrize("amount,region,zip_code,weight,expected", [
    (49999, "METRO", "06236", 1000, 3000),
    (50000, "METRO", "06236", 1000, 0),
    (50001, "LOCAL", "06236", 2001, 1000),
    (49999, "LOCAL", "63000", 3000, 7500),
    (10000, "LOCAL", "23100", 6000, 11000),
    (80000, "METRO", "40200", 12000, 9000),
])
def test_calc_shipping_fee(amount, region, zip_code, weight, expected):
    assert shipping.calc_shipping_fee(amount, region, zip_code, weight) == expected


def test_calc_shipping_fee_free_shipping_skips_region_check():
    assert shipping.calc_shipping_fee(50000, "MARS", "06236", 100) == 0
    with pytest.raises(ValueError):
        shipping.calc_shipping_fee(49999, "MARS", "06236", 100)


def test_calc_bundle_shipping_fee():
    items = [
        {"seller_id": 1, "amount": 30000, "weight_g": 1000},
        {"seller_id": 1, "amount": 20000, "weight_g": 1500},
        {"seller_id": 2, "amount": 10000, "weight_g": 500},
    ]
    # 판매자1: 50000원(무료) + 2500g(1000원), 판매자2: 3000원 + 0원
    assert shipping.calc_bundle_shipping_fee(items) == 4000


@pytest.mark.parametrize("amount,weight,expected", [
    (49999, 2000, 3000), (50000, 2000, 0), (49999, 2001, 4000), (50000, 10001, 4000),
])
def test_calc_bundle_shipping_fee_single_seller(amount, weight, expected):
    items = [{"seller_id": 7, "amount": amount, "weight_g": weight}]
    assert shipping.calc_bundle_shipping_fee(items) == expected


def test_calc_bundle_shipping_fee_separate_sellers_not_merged():
    items = [
        {"seller_id": 1, "amount": 25000, "weight_g": 1500},
        {"seller_id": 2, "amount": 25000, "weight_g": 1500},
    ]
    assert shipping.calc_bundle_shipping_fee(items) == 6000


def test_calc_bundle_shipping_fee_empty():
    assert shipping.calc_bundle_shipping_fee([]) == 0


@pytest.mark.parametrize("tracking_no,expected", [
    ("000000001234", True), ("0000-0000-1234", True), ("999999999994", True),
    ("012345678903", True), ("000000000000", True),
    ("000000001235", False), ("000000001233", False), ("999999999995", False),
    ("00000000123", False), ("0000000012340", False), ("00000000123a", False), ("", False),
])
def test_validate_tracking_number(tracking_no, expected):
    assert shipping.validate_tracking_number(tracking_no) is expected


@pytest.mark.parametrize("serial,expected", [
    (0, "000000000000"), (123, "000000001234"), (1234567890, "012345678903"),
    (99999999999, "999999999994"),
])
def test_make_tracking_number(serial, expected):
    assert shipping.make_tracking_number(serial) == expected
    assert shipping.validate_tracking_number(expected) is True


@pytest.mark.parametrize("serial", [-1, 10 ** 11])
def test_make_tracking_number_out_of_range(serial):
    with pytest.raises(ValueError):
        shipping.make_tracking_number(serial)


def test_format_tracking_number():
    assert shipping.format_tracking_number("012345678903") == "0123-4567-8903"
    assert shipping.format_tracking_number("0123-4567-8903") == "0123-4567-8903"


def test_format_tracking_number_invalid():
    with pytest.raises(ValueError):
        shipping.format_tracking_number("012345678904")


@pytest.mark.parametrize("current,new,expected", [
    ("READY", "PICKED", True), ("READY", "CANCELLED", True), ("READY", "SHIPPED", False),
    ("PICKED", "SHIPPED", True), ("PICKED", "CANCELLED", True), ("PICKED", "READY", False),
    ("SHIPPED", "IN_TRANSIT", True), ("SHIPPED", "RETURNED", True), ("SHIPPED", "CANCELLED", False),
    ("IN_TRANSIT", "DELIVERED", True), ("IN_TRANSIT", "RETURNED", True), ("IN_TRANSIT", "SHIPPED", False),
    ("DELIVERED", "RETURNED", True), ("DELIVERED", "CANCELLED", False),
    ("RETURNED", "READY", False), ("CANCELLED", "READY", False), ("READY", "READY", False),
])
def test_can_transition(current, new, expected):
    assert shipping.can_transition(current, new) is expected


def test_can_transition_unknown_status():
    with pytest.raises(ValueError):
        shipping.can_transition("LOST", "READY")


@pytest.mark.parametrize("order_date,hour,expected", [
    (D(2024, 6, 3), 0, D(2024, 6, 3)),     # 월요일
    (D(2024, 6, 3), 13, D(2024, 6, 3)),
    (D(2024, 6, 3), 14, D(2024, 6, 4)),
    (D(2024, 6, 3), 23, D(2024, 6, 4)),
    (D(2024, 6, 5), 15, D(2024, 6, 7)),    # 6/6 현충일
    (D(2024, 6, 6), 9, D(2024, 6, 7)),     # 휴일 주문
    (D(2024, 6, 8), 9, D(2024, 6, 10)),    # 토요일 주문
])
def test_calc_ship_date(order_date, hour, expected):
    assert shipping.calc_ship_date(order_date, hour) == expected


@pytest.mark.parametrize("hour", [-1, 24])
def test_calc_ship_date_invalid_hour(hour):
    with pytest.raises(ValueError):
        shipping.calc_ship_date(D(2024, 6, 3), hour)


@pytest.mark.parametrize("ship_date,zip_code,expected", [
    (D(2024, 6, 3), "06236", D(2024, 6, 4)),
    (D(2024, 6, 3), "63000", D(2024, 6, 5)),
    (D(2024, 6, 3), "23100", D(2024, 6, 7)),
    (D(2024, 6, 5), "06236", D(2024, 6, 7)),
])
def test_estimate_arrival_date(ship_date, zip_code, expected):
    assert shipping.estimate_arrival_date(ship_date, zip_code) == expected


@pytest.mark.parametrize("delivered,today,expected", [
    (D(2024, 6, 5), D(2024, 6, 10), False),
    (D(2024, 6, 6), D(2024, 6, 6), True),
    (D(2024, 6, 4), D(2024, 6, 10), False),
    (None, D(2024, 6, 5), False),
    (None, D(2024, 6, 4), False),
    (None, D(2024, 6, 6), True),
])
def test_is_delayed(delivered, today, expected):
    assert shipping.is_delayed(D(2024, 6, 5), delivered, today) is expected


@pytest.mark.parametrize("amount,delivered,expected", [
    (100000, D(2024, 5, 31), 0),
    (100000, D(2024, 6, 1), 0),
    (100000, D(2024, 6, 2), 0),
    (100000, D(2024, 6, 3), 2000),
    (100000, D(2024, 6, 4), 3000),
    (100000, D(2024, 6, 11), 10000),
    (100000, D(2024, 6, 12), 10000),
    (12345, D(2024, 6, 3), 240),
])
def test_calc_delay_compensation(amount, delivered, expected):
    assert shipping.calc_delay_compensation(amount, D(2024, 6, 1), delivered) == expected


@pytest.mark.parametrize("reason,expected", [
    ("DEFECT", "SELLER"), ("WRONG_ITEM", "SELLER"), ("DAMAGED", "SELLER"), ("LATE", "SELLER"),
    ("CHANGE_MIND", "BUYER"), ("SIZE", "BUYER"), ("WRONG_ORDER", "BUYER"),
])
def test_decide_return_fee_payer(reason, expected):
    assert shipping.decide_return_fee_payer(reason) == expected


def test_decide_return_fee_payer_unknown():
    with pytest.raises(ValueError):
        shipping.decide_return_fee_payer("UNKNOWN")


@pytest.mark.parametrize("reason,paid,zip_code,expected", [
    ("DEFECT", 0, "63000", 0),
    ("LATE", 3000, "06236", 0),
    ("CHANGE_MIND", 3000, "06236", 3000),
    ("SIZE", 0, "06236", 6000),
    ("CHANGE_MIND", 3000, "63000", 6000),
    ("WRONG_ORDER", 0, "23100", 11000),
])
def test_calc_return_fee(reason, paid, zip_code, expected):
    assert shipping.calc_return_fee(reason, paid, zip_code) == expected


def test_calc_return_fee_unknown_reason():
    with pytest.raises(ValueError):
        shipping.calc_return_fee("X", 0, "06236")


@pytest.mark.parametrize("request_date,expected", [
    (D(2024, 5, 31), False), (D(2024, 6, 1), True), (D(2024, 6, 7), True),
    (D(2024, 6, 8), True), (D(2024, 6, 9), False),
])
def test_is_return_window_open(request_date, expected):
    assert shipping.is_return_window_open(D(2024, 6, 1), request_date) is expected


@pytest.fixture
def sconn(conn):
    shipping.ensure_shipment_schema(conn)
    return conn


def test_ensure_shipment_schema_idempotent(sconn):
    shipping.ensure_shipment_schema(sconn)
    assert db.query_all(sconn, "SELECT * FROM shipments") == []


def test_create_shipment(sconn):
    sid = shipping.create_shipment(sconn, 10, "0123-4567-8903", "63000")
    assert sid == 1
    row = db.query_one(sconn, "SELECT * FROM shipments WHERE id = ?", (sid,))
    assert row["order_id"] == 10
    assert row["tracking_no"] == "012345678903"
    assert row["zip_code"] == "63000"
    assert row["status"] == "READY"
    assert row["updated_at"] is None
    assert row["delivered_at"] is None


@pytest.mark.parametrize("tracking_no,zip_code", [("012345678904", "06236"), ("012345678903", "6236")])
def test_create_shipment_invalid(sconn, tracking_no, zip_code):
    with pytest.raises(ValueError):
        shipping.create_shipment(sconn, 10, tracking_no, zip_code)
    assert db.query_all(sconn, "SELECT * FROM shipments") == []


def test_update_shipment_status_flow(sconn):
    sid = shipping.create_shipment(sconn, 10, "000000001234", "06236")
    assert shipping.update_shipment_status(sconn, sid, "PICKED", datetime(2024, 6, 3, 9, 0, 0)) == "PICKED"
    row = db.query_one(sconn, "SELECT * FROM shipments WHERE id = ?", (sid,))
    assert row["status"] == "PICKED"
    assert row["updated_at"] == "2024-06-03 09:00:00"
    assert row["delivered_at"] is None
    shipping.update_shipment_status(sconn, sid, "SHIPPED", datetime(2024, 6, 3, 15, 0, 0))
    shipping.update_shipment_status(sconn, sid, "IN_TRANSIT", datetime(2024, 6, 3, 20, 0, 0))
    row = db.query_one(sconn, "SELECT * FROM shipments WHERE id = ?", (sid,))
    assert row["delivered_at"] is None
    assert shipping.update_shipment_status(sconn, sid, "DELIVERED", datetime(2024, 6, 4, 11, 30, 5)) == "DELIVERED"
    row = db.query_one(sconn, "SELECT * FROM shipments WHERE id = ?", (sid,))
    assert row["status"] == "DELIVERED"
    assert row["updated_at"] == "2024-06-04 11:30:05"
    assert row["delivered_at"] == "2024-06-04 11:30:05"


def test_update_shipment_status_only_target_row(sconn):
    a = shipping.create_shipment(sconn, 1, "000000001234", "06236")
    b = shipping.create_shipment(sconn, 2, "000000000000", "06236")
    shipping.update_shipment_status(sconn, b, "PICKED", datetime(2024, 6, 3, 9, 0, 0))
    assert db.query_one(sconn, "SELECT status FROM shipments WHERE id = ?", (a,))["status"] == "READY"
    assert db.query_one(sconn, "SELECT status FROM shipments WHERE id = ?", (b,))["status"] == "PICKED"


def test_update_shipment_status_illegal(sconn):
    sid = shipping.create_shipment(sconn, 10, "000000001234", "06236")
    with pytest.raises(ValueError):
        shipping.update_shipment_status(sconn, sid, "DELIVERED", datetime(2024, 6, 3, 9, 0, 0))
    row = db.query_one(sconn, "SELECT * FROM shipments WHERE id = ?", (sid,))
    assert row["status"] == "READY"
    assert row["updated_at"] is None


def test_update_shipment_status_missing(sconn):
    with pytest.raises(ValueError):
        shipping.update_shipment_status(sconn, 999, "PICKED", datetime(2024, 6, 3, 9, 0, 0))
