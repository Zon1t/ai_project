# -*- coding: utf-8 -*-
"""주문 관리."""
from datetime import datetime
from typing import Dict, List

from order_settlement import config, db

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

VALID_TRANSITIONS = {
    "CREATED": ["PAID", "CANCELLED"],
    "PAID": ["SHIPPED", "CANCELLED"],
    "SHIPPED": ["DELIVERED"],
    "DELIVERED": [],
    "CANCELLED": [],
}


def create_order(conn, customer_id, coupon_codes=None, created_at=None):
    """주문 생성. created_at은 UTC 기준."""
    if created_at is None:
        created_at = datetime.utcnow()
    return db.insert(conn, "orders", {
        "customer_id": customer_id,
        "status": "CREATED",
        "coupon_codes": ",".join(coupon_codes or []),
        "created_at": created_at.strftime(DATE_FORMAT),
    })


def add_item(conn, order_id, product_name, unit_price, quantity):
    if unit_price < 0 or quantity <= 0:
        raise ValueError("잘못된 상품 정보: %s, %s, %s" % (product_name, unit_price, quantity))
    return db.insert(conn, "order_items", {
        "order_id": order_id,
        "product_name": product_name,
        "unit_price": unit_price,
        "quantity": quantity,
    })


def remove_item(conn, item_id):
    cur = db.execute(conn, "DELETE FROM order_items WHERE id = ?", (item_id,))
    return cur.rowcount > 0


def get_items(conn, order_id):
    # type: (object, int) -> List[Dict]
    return db.query_all(conn, "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,))


def calc_subtotal(items):
    return sum(i["unit_price"] * i["quantity"] for i in items)


def calc_shipping_fee(subtotal):
    if subtotal == 0:
        return 0
    if subtotal >= config.FREE_SHIPPING_THRESHOLD:
        return 0
    return config.SHIPPING_FEE


def calc_order_total(items):
    subtotal = calc_subtotal(items)
    return subtotal + calc_shipping_fee(subtotal)


def get_order(conn, order_id):
    order = db.query_one(conn, "SELECT * FROM orders WHERE id = ?", (order_id,))
    if order is None:
        return None
    order["items"] = get_items(conn, order_id)
    order["coupon_codes"] = [c for c in (order["coupon_codes"] or "").split(",") if c]
    return order


def list_orders_by_status(conn, status):
    sql = "SELECT * FROM orders WHERE status = '%s' ORDER BY id" % status
    return db.query_all(conn, sql)


def list_orders_by_customer(conn, customer_id):
    return db.query_all(conn, "SELECT * FROM orders WHERE customer_id = ? ORDER BY id", (customer_id,))


def is_cancelable(status):
    return "CANCELLED" in VALID_TRANSITIONS.get(status, [])


def change_status(conn, order_id, new_status):
    order = db.query_one(conn, "SELECT * FROM orders WHERE id = ?", (order_id,))
    if order is None:
        raise ValueError("주문 없음: %s" % order_id)
    if new_status not in VALID_TRANSITIONS.get(order["status"], []):
        raise ValueError("상태 변경 불가: %s -> %s" % (order["status"], new_status))
    db.execute(conn, "UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
    return new_status


def cancel_order(conn, order_id):
    return change_status(conn, order_id, "CANCELLED")
