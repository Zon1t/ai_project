# -*- coding: utf-8 -*-
from datetime import date, timedelta

import pytest

from order_settlement import db, seller

D = date(2024, 3, 1)
VALID_BIZ = "220-81-12341"


def days(n):
    return D + timedelta(days=n)


@pytest.mark.parametrize("count,total,expected", [
    (0, 0, 0), (1, 0, 0), (5, -1, 0), (1, 3, 333), (2, 3, 666), (50, 1000, 50), (3, 1, 3000), (0, 7, 0),
])
def test_calc_rate_permille(count, total, expected):
    assert seller.calc_rate_permille(count, total) == expected


@pytest.mark.parametrize("sales,cancel,active,expected", [
    (100000000, 0, 89, "NEW"),
    (50000000, 20, 90, "PREMIUM"),
    (50000000, 19, 365, "PREMIUM"),
    (49999999, 20, 90, "EXCELLENT"),
    (50000000, 21, 90, "EXCELLENT"),
    (10000000, 30, 90, "EXCELLENT"),
    (9999999, 0, 90, "GENERAL"),
    (10000000, 31, 90, "GENERAL"),
    (0, 0, 90, "GENERAL"),
])
def test_calc_seller_grade(sales, cancel, active, expected):
    assert seller.calc_seller_grade(sales, cancel, active) == expected


@pytest.mark.parametrize("grade,expected", [("PREMIUM", 10000), ("EXCELLENT", 3000), ("GENERAL", 1000), ("NEW", 100)])
def test_get_product_limit(grade, expected):
    assert seller.get_product_limit(grade) == expected


def test_get_product_limit_unknown():
    with pytest.raises(ValueError):
        seller.get_product_limit("GOLD")


@pytest.mark.parametrize("grade,current,adding,expected", [
    ("GENERAL", 999, 1, True), ("GENERAL", 1000, 1, False), ("GENERAL", 998, 2, True), ("GENERAL", 999, 2, False),
    ("NEW", 0, 100, True), ("NEW", 0, 101, False), ("PREMIUM", 9999, 1, True),
])
def test_can_register_product(grade, current, adding, expected):
    assert seller.can_register_product(grade, current, adding) is expected


def test_can_register_product_default_and_invalid():
    assert seller.can_register_product("NEW", 99) is True
    assert seller.can_register_product("NEW", 100) is False
    for adding in (0, -1):
        with pytest.raises(ValueError):
            seller.can_register_product("NEW", 0, adding)
    with pytest.raises(ValueError):
        seller.can_register_product("GOLD", 0, 1)


@pytest.mark.parametrize("offset,expected", [(0, True), (89, True), (90, False), (91, False)])
def test_is_new_seller(offset, expected):
    assert seller.is_new_seller(D, days(offset)) is expected


@pytest.mark.parametrize("offset,claims,orders,sanction,expected", [
    (10, 0, 0, "SUSPEND", "SANCTIONED"),
    (400, 0, 100, "TERMINATE", "SANCTIONED"),
    (10, 0, 100, "WARNING", "NEW_SELLER"),
    (89, 50, 100, "NONE", "NEW_SELLER"),
    (90, 1, 20, "NONE", "HIGH_CLAIM"),
    (90, 0, 20, "NONE", None),
    (90, 49, 1000, "NONE", None),
    (90, 50, 1000, "NONE", "HIGH_CLAIM"),
    (90, 19, 19, "NONE", None),
    (90, 2, 21, "WARNING", "HIGH_CLAIM"),
    (90, 1, 21, "NONE", None),
])
def test_decide_settlement_hold(offset, claims, orders, sanction, expected):
    assert seller.decide_settlement_hold(D, days(offset), claims, orders, sanction) == expected


def test_decide_settlement_hold_default():
    assert seller.decide_settlement_hold(D, days(90), 0, 100) is None
    assert seller.decide_settlement_hold(D, days(1), 0, 100) == "NEW_SELLER"


@pytest.mark.parametrize("last,offset,expected", [
    (None, 0, True), (D, 0, False), (D, 179, False), (D, 180, True), (D, 181, True),
])
def test_is_dormant_seller(last, offset, expected):
    assert seller.is_dormant_seller(last, days(offset)) is expected


@pytest.mark.parametrize("violations,expected", [
    ([], 0),
    (["LATE_SHIPMENT"], 1),
    (["UNFULFILLED"], 3),
    (["FALSE_INFO"], 5),
    (["FRAUD"], 50),
    (["LATE_SHIPMENT"] * 3 + ["UNFULFILLED", "FALSE_INFO"], 11),
])
def test_calc_penalty_points(violations, expected):
    assert seller.calc_penalty_points(violations) == expected


def test_calc_penalty_points_unknown():
    with pytest.raises(ValueError):
        seller.calc_penalty_points(["LATE_SHIPMENT", "SPAM"])


@pytest.mark.parametrize("prev,new,clean,expected", [
    (21, 4, 0, 25), (21, 4, 5, 25), (21, 4, 6, 14), (21, 4, 7, 14), (0, 3, 12, 3), (21, 0, 6, 10),
])
def test_calc_total_penalty(prev, new, clean, expected):
    assert seller.calc_total_penalty(prev, new, clean) == expected


@pytest.mark.parametrize("points,expected", [
    (0, "NONE"), (9, "NONE"), (10, "WARNING"), (29, "WARNING"), (30, "SUSPEND"), (49, "SUSPEND"),
    (50, "TERMINATE"), (80, "TERMINATE"),
])
def test_get_sanction_level(points, expected):
    assert seller.get_sanction_level(points) == expected


@pytest.mark.parametrize("points,expected", [
    (0, 0), (29, 0), (30, 7), (32, 7), (33, 12), (35, 12), (36, 17), (42, 27), (44, 27), (45, 30), (49, 30),
    (50, -1), (90, -1),
])
def test_calc_suspend_days(points, expected):
    assert seller.calc_suspend_days(points) == expected


@pytest.mark.parametrize("shipped,today,expected", [
    (2, 2, False), (3, 3, False), (4, 4, True), (3, 10, False), (0, 10, False),
    (None, 3, False), (None, 4, True),
])
def test_is_shipment_delayed(shipped, today, expected):
    shipped_date = days(shipped) if shipped is not None else None
    assert seller.is_shipment_delayed(D, shipped_date, days(today)) is expected


def test_calc_delay_rate():
    shipments = [
        {"order_date": D, "shipped_date": days(4)},
        {"order_date": D, "shipped_date": days(3)},
        {"order_date": D},
        {"order_date": D, "shipped_date": None},
    ]
    assert seller.calc_delay_rate(shipments, days(4)) == 750
    assert seller.calc_delay_rate(shipments, days(3)) == 250
    assert seller.calc_delay_rate(shipments[1:2], days(10)) == 0
    assert seller.calc_delay_rate([], days(10)) == 0


@pytest.mark.parametrize("number,expected", [
    (VALID_BIZ, True), ("2208112341", True), ("105-87-12345", True), ("123-45-67891", True), ("314-86-10060", True),
    ("220-81-12342", False), ("105-87-12340", False), ("123-45-67890", False),
    ("220-81-1234", False), ("220-81-123411", False), ("220-81-1234a", False), ("", False), (None, False),
])
def test_validate_business_no(number, expected):
    assert seller.validate_business_no(number) is expected


def _info(**kw):
    info = {"business_no": VALID_BIZ, "company_name": "가나상회", "representative": "홍길동", "address": "서울시 중구"}
    info.update(kw)
    return info


def test_validate_business_info_ok():
    assert seller.validate_business_info(_info()) is True


@pytest.mark.parametrize("field", ["business_no", "company_name", "representative", "address"])
def test_validate_business_info_missing(field):
    with pytest.raises(ValueError, match=field):
        seller.validate_business_info(_info(**{field: "  "}))
    info = _info()
    del info[field]
    with pytest.raises(ValueError, match=field):
        seller.validate_business_info(info)
    with pytest.raises(ValueError, match=field):
        seller.validate_business_info(_info(**{field: None}))


def test_validate_business_info_multiple_missing():
    with pytest.raises(ValueError, match="company_name, address"):
        seller.validate_business_info(_info(company_name="", address=""))


def test_validate_business_info_bad_number():
    with pytest.raises(ValueError, match="사업자등록번호"):
        seller.validate_business_info(_info(business_no="220-81-12342"))


@pytest.mark.parametrize("grade,small,expected", [
    ("GENERAL", False, 120), ("NEW", False, 120), ("EXCELLENT", False, 105), ("PREMIUM", False, 90),
    ("GENERAL", True, 110), ("EXCELLENT", True, 95), ("PREMIUM", True, 85),
])
def test_calc_commission_rate(grade, small, expected):
    assert seller.calc_commission_rate(grade, small) == expected


def test_calc_commission_rate_default():
    assert seller.calc_commission_rate("PREMIUM") == 90


@pytest.mark.parametrize("amount,grade,small,expected", [
    (100000, "GENERAL", False, 12000), (12345, "PREMIUM", False, 1111), (100000, "EXCELLENT", True, 9500),
    (999, "GENERAL", False, 119), (0, "GENERAL", False, 0),
])
def test_calc_commission(amount, grade, small, expected):
    assert seller.calc_commission(amount, grade, small) == expected


def test_calc_commission_default_and_invalid():
    assert seller.calc_commission(100000, "EXCELLENT") == 10500
    with pytest.raises(ValueError):
        seller.calc_commission(-1, "GENERAL")


@pytest.fixture
def sconn(conn):
    seller.ensure_seller_schema(conn)
    seller.ensure_seller_schema(conn)
    return conn


def test_insert_seller(sconn):
    sid = seller.insert_seller(sconn, _info(), "2024-03-01 09:00:00")
    assert sid == 1
    row = db.query_one(sconn, "SELECT * FROM sellers WHERE id = ?", (sid,))
    assert row == {
        "id": 1, "company_name": "가나상회", "business_no": VALID_BIZ, "grade": "NEW",
        "joined_at": "2024-03-01 09:00:00", "last_sale_at": None, "status": "ACTIVE",
    }


def test_insert_seller_invalid(sconn):
    with pytest.raises(ValueError):
        seller.insert_seller(sconn, _info(business_no="220-81-12342"), "2024-03-01 09:00:00")
    with pytest.raises(ValueError):
        seller.insert_seller(sconn, _info(representative=""), "2024-03-01 09:00:00")
    assert db.query_all(sconn, "SELECT * FROM sellers") == []


def test_mark_dormant_sellers(sconn):
    today = date(2024, 9, 1)
    lasts = [
        seller.date_utils.format_date(today - timedelta(days=180)),
        seller.date_utils.format_date(today - timedelta(days=179)),
        None,
        seller.date_utils.format_date(today - timedelta(days=400)),
    ]
    for i, last in enumerate(lasts):
        sid = seller.insert_seller(sconn, _info(), "2024-01-01 09:00:00")
        db.execute(sconn, "UPDATE sellers SET last_sale_at = ? WHERE id = ?", (last, sid))
    db.execute(sconn, "UPDATE sellers SET status = 'SUSPENDED' WHERE id = 4")
    assert seller.mark_dormant_sellers(sconn, today) == [1, 3]
    statuses = [r["status"] for r in db.query_all(sconn, "SELECT status FROM sellers ORDER BY id")]
    assert statuses == ["DORMANT", "ACTIVE", "DORMANT", "SUSPENDED"]
    assert seller.mark_dormant_sellers(sconn, today) == []
    assert seller.mark_dormant_sellers(sconn, today + timedelta(days=1)) == [2]
