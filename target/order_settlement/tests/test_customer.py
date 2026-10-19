# -*- coding: utf-8 -*-
import pytest

from order_settlement import customer, customer_dao


@pytest.mark.parametrize("email,ok", [
    ("minsu.kim@example.com", True),
    ("a+b@sub.example.co.kr", True),
    ("no-at-sign.example.com", False),
    ("user@nodot", False),
    ("", False),
])
def test_validate_email(email, ok):
    assert customer.validate_email(email) is ok


@pytest.mark.parametrize("phone,ok", [
    ("010-1234-5678", True),
    ("01012345678", True),
    ("011-123-4567", True),
    ("02-123-4567", False),
    ("010-12-5678", False),
])
def test_validate_phone(phone, ok):
    assert customer.validate_phone(phone) is ok


def test_normalize_phone():
    assert customer.normalize_phone("01012345678") == "010-1234-5678"
    assert customer.normalize_phone("011 123 4567") == "011-123-4567"
    with pytest.raises(ValueError):
        customer.normalize_phone("12345")


@pytest.mark.parametrize("name,masked", [("김민수", "김*수"), ("남궁민수", "남**수"), ("이수", "이*"), ("김", "김")])
def test_mask_name(name, masked):
    assert customer.mask_name(name) == masked


def test_mask_phone_and_email():
    assert customer.mask_phone("01012345678") == "010-****-5678"
    assert customer.mask_email("minsu.kim@example.com") == "mi*******@example.com"
    assert customer.mask_email("a@example.com") == "a*@example.com"


def test_register_customer(conn):
    cid = customer.register_customer(conn, "최유나", "yuna@example.com", "01055556666", "2024-02-01 00:00:00")
    row = customer_dao.find_by_id(conn, cid)
    assert row["phone"] == "010-5555-6666"
    assert row["grade"] == "BASIC"


def test_register_customer_rejects(conn):
    customer.register_customer(conn, "최유나", "yuna@example.com", "01055556666", "2024-02-01 00:00:00")
    with pytest.raises(ValueError):
        customer.register_customer(conn, "최유나", "yuna@example.com", "01055556666")
    with pytest.raises(ValueError):
        customer.register_customer(conn, "최유나", "bad-email", "01055556666")
    with pytest.raises(ValueError):
        customer.register_customer(conn, "최유나", "yuna2@example.com", "02-123-4567")


def test_grade_discount_rate():
    assert customer.get_grade_discount_rate("GOLD") == 5
    assert customer.get_grade_discount_rate("NONE") == 0


@pytest.mark.parametrize("total,grade", [
    (0, "BASIC"), (299999, "BASIC"), (300000, "SILVER"), (999999, "SILVER"),
    (1000000, "GOLD"), (3000000, "VIP"),
])
def test_decide_grade(total, grade):
    assert customer.decide_grade(total) == grade


def test_upgrade_grade(seeded):
    assert customer.upgrade_grade(seeded, 1, 1000000) == "GOLD"
    assert customer_dao.find_by_id(seeded, 1)["grade"] == "GOLD"


def test_upgrade_grade_never_downgrades(seeded):
    assert customer.upgrade_grade(seeded, 2, 0) == "GOLD"
    assert customer_dao.find_by_id(seeded, 2)["grade"] == "GOLD"


def test_upgrade_grade_missing(seeded):
    with pytest.raises(ValueError):
        customer.upgrade_grade(seeded, 99, 0)


def test_customer_summary(seeded):
    s = customer.customer_summary(seeded, 1)
    assert s == {"id": 1, "name": "김*수", "email": "mi*******@example.com", "phone": "010-****-5678",
                 "grade": "BASIC"}
    assert customer.customer_summary(seeded, 99) is None
