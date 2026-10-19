# -*- coding: utf-8 -*-
from datetime import datetime

import pytest

from order_settlement import order

T0 = datetime(2024, 3, 10, 1, 0, 0)


def _order_with_items(conn):
    oid = order.create_order(conn, 1, ["CPA"], created_at=T0)
    order.add_item(conn, oid, "키보드", 30000, 2)
    order.add_item(conn, oid, "마우스", 15000, 1)
    return oid


def test_create_and_get_order(seeded):
    oid = _order_with_items(seeded)
    o = order.get_order(seeded, oid)
    assert o["status"] == "CREATED"
    assert o["coupon_codes"] == ["CPA"]
    assert o["created_at"] == "2024-03-10 01:00:00"
    assert [i["product_name"] for i in o["items"]] == ["키보드", "마우스"]


def test_get_order_missing(conn):
    assert order.get_order(conn, 99) is None


def test_order_without_coupons(seeded):
    oid = order.create_order(seeded, 1, created_at=T0)
    assert order.get_order(seeded, oid)["coupon_codes"] == []


def test_add_item_invalid(seeded):
    oid = order.create_order(seeded, 1, created_at=T0)
    with pytest.raises(ValueError):
        order.add_item(seeded, oid, "X", -1, 1)
    with pytest.raises(ValueError):
        order.add_item(seeded, oid, "X", 1000, 0)


def test_remove_item(seeded):
    oid = _order_with_items(seeded)
    item_id = order.get_items(seeded, oid)[0]["id"]
    assert order.remove_item(seeded, item_id) is True
    assert order.remove_item(seeded, item_id) is False
    assert len(order.get_items(seeded, oid)) == 1


def test_calc_subtotal_and_total(seeded):
    items = order.get_items(seeded, _order_with_items(seeded))
    assert order.calc_subtotal(items) == 75000
    assert order.calc_order_total(items) == 75000
    assert order.calc_order_total([{"unit_price": 10000, "quantity": 2}]) == 23000


@pytest.mark.parametrize("subtotal,fee", [(0, 0), (49999, 3000), (50000, 0), (120000, 0)])
def test_shipping_fee(subtotal, fee):
    assert order.calc_shipping_fee(subtotal) == fee


def test_list_orders(seeded):
    o1 = order.create_order(seeded, 1, created_at=T0)
    o2 = order.create_order(seeded, 2, created_at=T0)
    order.change_status(seeded, o2, "PAID")
    assert [o["id"] for o in order.list_orders_by_status(seeded, "CREATED")] == [o1]
    assert [o["id"] for o in order.list_orders_by_customer(seeded, 2)] == [o2]


@pytest.mark.parametrize("status,ok", [
    ("CREATED", True), ("PAID", True), ("SHIPPED", False), ("DELIVERED", False), ("CANCELLED", False),
])
def test_is_cancelable(status, ok):
    assert order.is_cancelable(status) is ok


def test_change_status_flow(seeded):
    oid = order.create_order(seeded, 1, created_at=T0)
    assert order.change_status(seeded, oid, "PAID") == "PAID"
    assert order.change_status(seeded, oid, "SHIPPED") == "SHIPPED"
    with pytest.raises(ValueError):
        order.change_status(seeded, oid, "CANCELLED")
    with pytest.raises(ValueError):
        order.change_status(seeded, 99, "PAID")


def test_cancel_order(seeded):
    oid = order.create_order(seeded, 1, created_at=T0)
    assert order.cancel_order(seeded, oid) == "CANCELLED"
    assert order.get_order(seeded, oid)["status"] == "CANCELLED"
