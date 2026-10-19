# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

import pytest

from order_settlement import coupon

NOW = datetime(2024, 3, 1, 0, 0, 0)


def test_generate_coupon_code():
    code = coupon.generate_coupon_code(1, NOW)
    assert code == coupon.generate_coupon_code(1, NOW)
    assert code != coupon.generate_coupon_code(2, NOW)
    assert code.startswith("CP") and len(code) == 12


def test_create_and_find(conn):
    coupon.create_coupon(conn, "WELCOME", "FIXED", 5000)
    c = coupon.find_coupon(conn, "WELCOME")
    assert c["min_order"] == 100000
    assert c["used"] == 0 and c["expires_at"] is None
    assert coupon.find_coupon(conn, "NONE") is None


@pytest.mark.parametrize("ctype,value", [("POINT", 10), ("RATE", 0), ("RATE", 101)])
def test_create_invalid(conn, ctype, value):
    with pytest.raises(ValueError):
        coupon.create_coupon(conn, "BAD", ctype, value)


def test_is_expired():
    c = {"expires_at": "2024-03-31 00:00:00"}
    assert coupon.is_expired(c, NOW) is False
    assert coupon.is_expired(c, datetime(2024, 3, 31, 0, 0, 1)) is True
    assert coupon.is_expired({"expires_at": None}, NOW) is False


def test_validate_coupon():
    base = {"used": 0, "expires_at": "2024-03-31 00:00:00", "customer_id": 1}
    assert coupon.validate_coupon(None) == (False, "NOT_FOUND")
    assert coupon.validate_coupon(dict(base, used=1), 1, NOW) == (False, "USED")
    assert coupon.validate_coupon(base, 1, datetime(2024, 4, 1)) == (False, "EXPIRED")
    assert coupon.validate_coupon(base, 2, NOW) == (False, "NOT_OWNER")
    assert coupon.validate_coupon(base, 1, NOW) == (True, "OK")
    assert coupon.validate_coupon(dict(base, customer_id=None), 2, NOW) == (True, "OK")


def test_issue_coupon(conn):
    code = coupon.issue_coupon(conn, 1, "RATE", 10, now=NOW)
    c = coupon.find_coupon(conn, code)
    assert c["customer_id"] == 1
    assert c["expires_at"] == (NOW + timedelta(days=30)).strftime(coupon.DATE_FORMAT)


def test_mark_used(conn):
    coupon.create_coupon(conn, "ONCE", "FIXED", 3000)
    assert coupon.mark_used(conn, "ONCE") is True
    assert coupon.mark_used(conn, "ONCE") is False


def test_list_customer_coupons(conn):
    coupon.create_coupon(conn, "A1", "FIXED", 1000, customer_id=1)
    coupon.create_coupon(conn, "A2", "FIXED", 2000, customer_id=1)
    coupon.create_coupon(conn, "B1", "FIXED", 3000, customer_id=2)
    coupon.mark_used(conn, "A1")
    assert [c["code"] for c in coupon.list_customer_coupons(conn, 1)] == ["A2"]
    assert [c["code"] for c in coupon.list_customer_coupons(conn, 1, include_used=True)] == ["A1", "A2"]


def test_load_usable_coupons(conn):
    coupon.create_coupon(conn, "OK1", "FIXED", 1000, expires_at=datetime(2024, 12, 31))
    coupon.create_coupon(conn, "OLD", "FIXED", 1000, expires_at=datetime(2024, 1, 1))
    coupon.create_coupon(conn, "OTHER", "FIXED", 1000, customer_id=2)
    usable = coupon.load_usable_coupons(conn, ["OK1", "OLD", "NONE", "OTHER"], customer_id=1, now=NOW)
    assert [c["code"] for c in usable] == ["OK1"]
