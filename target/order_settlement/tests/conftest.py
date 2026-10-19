# -*- coding: utf-8 -*-
import pytest

from order_settlement import customer_dao, db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_schema(c)
    yield c
    c.close()


@pytest.fixture
def seeded(conn):
    """고객 4명 (동명이인 김민수 포함)."""
    customer_dao.insert_customer(conn, "김민수", "minsu.kim@example.com", "010-1234-5678", "BASIC",
                                 "2024-01-05 09:00:00")
    customer_dao.insert_customer(conn, "이서연", "seoyeon.lee@example.com", "010-2222-3333", "GOLD",
                                 "2024-01-06 10:00:00")
    customer_dao.insert_customer(conn, "박지훈", "jihoon.park@example.com", "010-4444-5678", "SILVER",
                                 "2024-01-07 11:00:00")
    customer_dao.insert_customer(conn, "김민수", "minsu.kim2@example.com", "010-9999-0000", "VIP",
                                 "2024-01-08 12:00:00")
    return conn
