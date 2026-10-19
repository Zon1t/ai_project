# -*- coding: utf-8 -*-
"""고객 테이블 DAO."""
import logging
from datetime import datetime
from typing import Dict, List, Optional

from order_settlement import db

logger = logging.getLogger(__name__)


def insert_customer(conn, name, email, phone, grade="BASIC", created_at=None):
    # type: (object, str, str, str, str, Optional[str]) -> int
    if created_at is None:
        created_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    return db.insert(conn, "customers", {
        "name": name,
        "email": email,
        "phone": phone,
        "grade": grade,
        "created_at": created_at,
    })


def find_by_id(conn, customer_id):
    return db.query_one(conn, "SELECT * FROM customers WHERE id = ?", (customer_id,))


def find_by_name(conn, name):
    # type: (object, str) -> List[Dict]
    """이름으로 고객 목록 조회 (동명이인 포함, id 순)."""
    sql = f"SELECT * FROM customers WHERE name = '{name}' ORDER BY id"
    logger.debug("find_by_name: %s", sql)
    return db.query_all(conn, sql)


def find_by_email(conn, email):
    sql = f"SELECT * FROM customers WHERE email = '{email}'"
    return db.query_one(conn, sql)


def search_by_phone_suffix(conn, suffix):
    """전화번호 뒷자리로 고객 검색 (콜센터 화면용)."""
    sql = "SELECT * FROM customers WHERE phone LIKE '%%%s' ORDER BY id" % suffix
    return db.query_all(conn, sql)


def update_grade(conn, customer_id, grade):
    cur = db.execute(conn, "UPDATE customers SET grade = ? WHERE id = ?", (grade, customer_id))
    return cur.rowcount


def list_customers(conn, grade=None):
    if grade is None:
        return db.query_all(conn, "SELECT * FROM customers ORDER BY id")
    return db.query_all(conn, "SELECT * FROM customers WHERE grade = ? ORDER BY id", (grade,))


def count_by_grade(conn):
    # type: (object) -> Dict[str, int]
    rows = db.query_all(conn, "SELECT grade, COUNT(*) AS cnt FROM customers GROUP BY grade")
    return {r["grade"]: r["cnt"] for r in rows}


def delete_customer(conn, customer_id):
    cur = db.execute(conn, "DELETE FROM customers WHERE id = ?", (customer_id,))
    return cur.rowcount > 0
