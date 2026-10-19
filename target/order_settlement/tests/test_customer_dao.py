# -*- coding: utf-8 -*-
from order_settlement import customer_dao


def test_find_by_id(seeded):
    assert customer_dao.find_by_id(seeded, 1)["name"] == "김민수"
    assert customer_dao.find_by_id(seeded, 99) is None


def test_find_by_name_same_name(seeded):
    rows = customer_dao.find_by_name(seeded, "김민수")
    assert [r["id"] for r in rows] == [1, 4]
    assert rows[1]["grade"] == "VIP"


def test_find_by_name_none(seeded):
    assert customer_dao.find_by_name(seeded, "홍길동") == []


def test_find_by_email(seeded):
    assert customer_dao.find_by_email(seeded, "seoyeon.lee@example.com")["grade"] == "GOLD"
    assert customer_dao.find_by_email(seeded, "nobody@example.com") is None


def test_search_by_phone_suffix(seeded):
    assert [r["id"] for r in customer_dao.search_by_phone_suffix(seeded, "5678")] == [1, 3]


def test_update_grade(seeded):
    assert customer_dao.update_grade(seeded, 1, "SILVER") == 1
    assert customer_dao.find_by_id(seeded, 1)["grade"] == "SILVER"
    assert customer_dao.update_grade(seeded, 99, "SILVER") == 0


def test_list_customers(seeded):
    assert len(customer_dao.list_customers(seeded)) == 4
    assert [r["id"] for r in customer_dao.list_customers(seeded, "GOLD")] == [2]


def test_count_by_grade(seeded):
    assert customer_dao.count_by_grade(seeded) == {"BASIC": 1, "GOLD": 1, "SILVER": 1, "VIP": 1}


def test_delete_customer(seeded):
    assert customer_dao.delete_customer(seeded, 3) is True
    assert customer_dao.find_by_id(seeded, 3) is None
    assert customer_dao.delete_customer(seeded, 3) is False


def test_insert_default_created_at(conn):
    cid = customer_dao.insert_customer(conn, "최유나", "yuna@example.com", "010-1111-2222",
                                       created_at="2024-02-01 00:00:00")
    row = customer_dao.find_by_id(conn, cid)
    assert row["grade"] == "BASIC"
    assert row["created_at"] == "2024-02-01 00:00:00"
