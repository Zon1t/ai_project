# -*- coding: utf-8 -*-
from order_settlement import db


def test_schema_tables(conn):
    rows = db.query_all(conn, "SELECT name FROM sqlite_master WHERE type = 'table'")
    names = {r["name"] for r in rows}
    assert {"customers", "orders", "order_items", "coupons", "payments", "settlements"} <= names


def test_insert_and_query(conn):
    first = db.insert(conn, "order_items", {"order_id": 1, "product_name": "A", "unit_price": 100, "quantity": 1})
    second = db.insert(conn, "order_items", {"order_id": 1, "product_name": "B", "unit_price": 200, "quantity": 2})
    assert (first, second) == (1, 2)
    row = db.query_one(conn, "SELECT * FROM order_items WHERE id = ?", (2,))
    assert row["product_name"] == "B" and row["quantity"] == 2
    assert len(db.query_all(conn, "SELECT * FROM order_items")) == 2


def test_query_one_missing(conn):
    assert db.query_one(conn, "SELECT * FROM orders WHERE id = ?", (99,)) is None


def test_execute_rowcount(conn):
    db.insert(conn, "orders", {"customer_id": 1, "status": "CREATED"})
    cur = db.execute(conn, "UPDATE orders SET status = ? WHERE id = ?", ("PAID", 1))
    assert cur.rowcount == 1


def test_get_connection_singleton():
    c1 = db.reset_db()
    assert db.get_connection() is c1
    c2 = db.reset_db()
    assert c2 is not c1
    assert db.query_all(c2, "SELECT * FROM customers") == []
