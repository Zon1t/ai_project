# -*- coding: utf-8 -*-
"""SQLite 접근 공통 모듈. 운영은 파일 DB, 테스트는 인메모리."""
import logging
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    grade TEXT NOT NULL DEFAULT 'BASIC',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'CREATED',
    coupon_codes TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    unit_price INTEGER NOT NULL,
    quantity INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS coupons (
    code TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    value INTEGER NOT NULL,
    min_order INTEGER NOT NULL,
    expires_at TEXT,
    customer_id INTEGER,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    method TEXT NOT NULL,
    card_number TEXT,
    fee INTEGER NOT NULL DEFAULT 0,
    refunded INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    paid_at TEXT
);
CREATE TABLE IF NOT EXISTS settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    commission INTEGER NOT NULL,
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING'
);
"""

_conn = None


def connect(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def get_connection():
    """프로세스 공용 커넥션 (최초 호출 시 생성)."""
    global _conn
    if _conn is None:
        _conn = connect(":memory:")
        init_schema(_conn)
    return _conn


def reset_db():
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = None
    return get_connection()


def execute(conn, sql, params=()):
    logger.debug("SQL 실행: %s %s", sql, params)
    cur = conn.execute(sql, params)
    conn.commit()
    return cur


def query_one(conn, sql, params=()):
    # type: (Any, str, tuple) -> Optional[Dict[str, Any]]
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def query_all(conn, sql, params=()):
    # type: (Any, str, tuple) -> List[Dict[str, Any]]
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def insert(conn, table, values):
    # type: (Any, str, Dict[str, Any]) -> int
    columns = ", ".join(values.keys())
    marks = ", ".join(["?"] * len(values))
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (table, columns, marks)
    cur = execute(conn, sql, tuple(values.values()))
    return cur.lastrowid
