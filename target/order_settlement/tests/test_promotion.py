# -*- coding: utf-8 -*-
from datetime import datetime

import pytest

from order_settlement import promotion

START = datetime(2024, 5, 1, 10, 0, 0)
END = datetime(2024, 5, 1, 12, 0, 0)
NOW = datetime(2024, 5, 1, 11, 0, 0)


def promo(pid, ptype, value, starts_at=START, ends_at=END, **extra):
    data = {"id": pid, "type": ptype, "value": value, "starts_at": starts_at, "ends_at": ends_at}
    data.update(extra)
    return data


@pytest.mark.parametrize("now,expected", [
    (datetime(2024, 5, 1, 9, 59, 59), "SCHEDULED"),
    (START, "ACTIVE"),
    (datetime(2024, 5, 1, 11, 59, 59), "ACTIVE"),
    (END, "ENDED"),
    (datetime(2024, 5, 2, 0, 0, 0), "ENDED"),
])
def test_get_promotion_status(now, expected):
    assert promotion.get_promotion_status(promo(1, "BUNDLE", 1), now) == expected
    assert promotion.is_promotion_active(promo(1, "BUNDLE", 1), now) is (expected == "ACTIVE")


def test_get_promotion_status_enabled_flag():
    assert promotion.get_promotion_status(promo(1, "BUNDLE", 1, enabled=False), NOW) == "DISABLED"
    assert promotion.get_promotion_status(promo(1, "BUNDLE", 1, enabled=False), END) == "DISABLED"
    assert promotion.get_promotion_status(promo(1, "BUNDLE", 1, enabled=True), NOW) == "ACTIVE"
    assert promotion.is_promotion_active(promo(1, "BUNDLE", 1, enabled=False), NOW) is False


@pytest.mark.parametrize("item", [
    promo(1, "TIME_SALE_RATE", 70),
    promo(1, "TIME_SALE_RATE", 1),
    promo(1, "TIME_SALE_FIXED", 100000),
    promo(1, "N_PLUS_ONE", 2),
    promo(1, "BUNDLE", 1, ends_at=datetime(2024, 5, 1, 10, 0, 1)),
])
def test_validate_promotion_ok(item):
    assert promotion.validate_promotion(item) is True


@pytest.mark.parametrize("item,msg", [
    (promo(1, "COUPON", 10), "유형"),
    (promo(1, "BUNDLE", 1, ends_at=START), "기간"),
    (promo(1, "BUNDLE", 1, starts_at=END, ends_at=START), "기간"),
    (promo(1, "TIME_SALE_FIXED", 0), "값"),
    (promo(1, "TIME_SALE_RATE", -1), "값"),
    (promo(1, "TIME_SALE_RATE", 71), "초과"),
])
def test_validate_promotion_error(item, msg):
    with pytest.raises(ValueError, match=msg):
        promotion.validate_promotion(item)


@pytest.mark.parametrize("price,sale_type,value,expected", [
    (10000, "TIME_SALE_RATE", 15, 8500),
    (9999, "TIME_SALE_RATE", 15, 8509),
    (10000, "TIME_SALE_RATE", 0, 10000),
    (10000, "TIME_SALE_FIXED", 3000, 7000),
    (1000, "TIME_SALE_FIXED", 899, 101),
    (1000, "TIME_SALE_FIXED", 900, 100),
    (1000, "TIME_SALE_FIXED", 901, 100),
    (1000, "TIME_SALE_RATE", 95, 100),
])
def test_calc_time_sale_price(price, sale_type, value, expected):
    assert promotion.calc_time_sale_price(price, sale_type, value) == expected


def test_calc_time_sale_price_unknown():
    with pytest.raises(ValueError, match="BUNDLE"):
        promotion.calc_time_sale_price(10000, "BUNDLE", 10)


@pytest.mark.parametrize("qty,buy_n,free_m,expected", [
    (0, 2, 1, 0),
    (2, 2, 1, 0),
    (3, 2, 1, 1),
    (5, 2, 1, 1),
    (6, 2, 1, 2),
    (6, 1, 1, 3),
    (3, 2, 2, 0),
    (4, 2, 2, 2),
    (7, 2, 2, 2),
    (8, 2, 2, 4),
])
def test_calc_free_quantity(qty, buy_n, free_m, expected):
    assert promotion.calc_free_quantity(qty, buy_n, free_m) == expected


def test_calc_free_quantity_default_free():
    assert promotion.calc_free_quantity(4, 1) == 2


@pytest.mark.parametrize("buy_n,free_m", [(0, 1), (-1, 1), (2, 0), (2, -1)])
def test_calc_free_quantity_error(buy_n, free_m):
    with pytest.raises(ValueError, match="N\\+M"):
        promotion.calc_free_quantity(10, buy_n, free_m)


def test_calc_n_plus_one_discount():
    assert promotion.calc_n_plus_one_discount(1500, 6, 2) == 3000
    assert promotion.calc_n_plus_one_discount(1500, 5, 2) == 1500
    assert promotion.calc_n_plus_one_discount(1500, 8, 2, 2) == 6000
    assert promotion.calc_n_plus_one_discount(1500, 2, 2) == 0


@pytest.mark.parametrize("qty,expected", [(0, 0), (1, 0), (2, 5), (3, 10), (4, 10), (5, 15), (6, 15)])
def test_get_bundle_rate(qty, expected):
    assert promotion.get_bundle_rate(qty) == expected


def test_calc_bundle_discount():
    assert promotion.calc_bundle_discount(12345, 3) == 1234
    assert promotion.calc_bundle_discount(12345, 2) == 617
    assert promotion.calc_bundle_discount(12345, 5) == 1851
    assert promotion.calc_bundle_discount(12345, 1) == 0


@pytest.mark.parametrize("category,item,expected", [
    ("FOOD", {"categories": ["FOOD", "BEAUTY"], "excluded_categories": ["ALCOHOL"]}, True),
    ("BEAUTY", {"categories": ["FOOD", "BEAUTY"], "excluded_categories": ["ALCOHOL"]}, True),
    ("ALCOHOL", {"categories": ["FOOD", "BEAUTY"], "excluded_categories": ["ALCOHOL"]}, False),
    ("TOY", {"categories": ["FOOD", "BEAUTY"], "excluded_categories": ["ALCOHOL"]}, False),
    ("FOOD", {"excluded_categories": ["FOOD"]}, False),
    ("TOY", {"excluded_categories": ["FOOD"]}, True),
    ("FOOD", {"categories": ["FOOD"], "excluded_categories": ["FOOD"]}, False),
    ("TOY", {}, True),
    ("TOY", {"categories": None, "excluded_categories": None}, True),
    ("TOY", {"categories": []}, True),
])
def test_is_category_eligible(category, item, expected):
    assert promotion.is_category_eligible(category, item) is expected


def test_filter_eligible_items():
    items = [{"name": "a", "category": "FOOD"}, {"name": "b", "category": "ALCOHOL"},
             {"name": "c", "category": "TOY"}, {"name": "d", "category": "FOOD"}]
    rule = {"categories": ["FOOD", "ALCOHOL"], "excluded_categories": ["ALCOHOL"]}
    assert [i["name"] for i in promotion.filter_eligible_items(items, rule)] == ["a", "d"]
    assert promotion.filter_eligible_items(items, {}) == items
    assert promotion.filter_eligible_items([], rule) == []


@pytest.mark.parametrize("qty,bought,limit,expected", [
    (1, 4, 5, "OK"),
    (2, 4, 5, "LIMIT_EXCEEDED"),
    (5, 0, 5, "OK"),
    (6, 0, 5, "LIMIT_EXCEEDED"),
    (1, 5, 5, "LIMIT_EXCEEDED"),
    (1, 7, 5, "LIMIT_EXCEEDED"),
    (3, 0, 3, "OK"),
    (4, 0, 3, "LIMIT_EXCEEDED"),
    (2, 1, 3, "OK"),
])
def test_check_purchase_limit(qty, bought, limit, expected):
    assert promotion.check_purchase_limit(qty, bought, limit) == expected


def test_check_purchase_limit_default():
    assert promotion.check_purchase_limit(5, 0) == "OK"
    assert promotion.check_purchase_limit(6, 0) == "LIMIT_EXCEEDED"


@pytest.mark.parametrize("qty", [0, -1])
def test_check_purchase_limit_error(qty):
    with pytest.raises(ValueError, match="수량"):
        promotion.check_purchase_limit(qty, 0)


@pytest.mark.parametrize("amount,discount,expected", [
    (100000, 45000, 40000),
    (100000, 40000, 40000),
    (100000, 39999, 39999),
    (200000, 60000, 50000),
    (125000, 60000, 50000),
    (12345, 5000, 4938),
    (12345, 0, 0),
])
def test_cap_promo_discount(amount, discount, expected):
    assert promotion.cap_promo_discount(amount, discount) == expected


def test_cap_promo_discount_custom_max():
    assert promotion.cap_promo_discount(100000, 20000, 10000) == 10000
    assert promotion.cap_promo_discount(100000, 9000, 10000) == 9000


@pytest.mark.parametrize("item,price,qty,expected", [
    (promo(1, "TIME_SALE_RATE", 20), 10000, 3, 6000),
    (promo(1, "TIME_SALE_FIXED", 3000), 10000, 2, 6000),
    (promo(1, "N_PLUS_ONE", 2), 1500, 6, 3000),
    (promo(1, "N_PLUS_ONE", 2), 1500, 2, 0),
    (promo(1, "BUNDLE", 1), 10000, 3, 3000),
    (promo(1, "BUNDLE", 1), 10000, 1, 0),
    (promo(1, "TIME_SALE_RATE", 70), 10000, 1, 4000),
    (promo(1, "TIME_SALE_FIXED", 9900), 10000, 1, 4000),
    (promo(1, "N_PLUS_ONE", 1), 1000, 2, 800),
])
def test_calc_promo_discount(item, price, qty, expected):
    assert promotion.calc_promo_discount(item, price, qty) == expected


def test_calc_promo_discount_unknown():
    with pytest.raises(ValueError, match="COUPON"):
        promotion.calc_promo_discount(promo(1, "COUPON", 10), 10000, 1)


def test_select_best_promotion_max_discount():
    promos = [
        promo(2, "BUNDLE", 1),
        promo(3, "TIME_SALE_FIXED", 5000, ends_at=NOW),
        promo(4, "TIME_SALE_FIXED", 5000, starts_at=datetime(2024, 5, 1, 11, 0, 1)),
        promo(1, "TIME_SALE_RATE", 20),
        promo(5, "TIME_SALE_FIXED", 1000),
    ]
    assert promotion.select_best_promotion(promos, 10000, 3, NOW) == (1, 6000)


def test_select_best_promotion_tie_priority():
    rate = promo(1, "TIME_SALE_RATE", 20)
    fixed = promo(2, "TIME_SALE_FIXED", 2000)
    assert promotion.select_best_promotion([fixed, rate], 10000, 3, NOW) == (1, 6000)
    assert promotion.select_best_promotion([rate, fixed], 10000, 3, NOW) == (1, 6000)
    same_a = promo(5, "TIME_SALE_RATE", 20)
    same_b = promo(6, "TIME_SALE_RATE", 20)
    assert promotion.select_best_promotion([same_a, same_b], 10000, 3, NOW) == (5, 6000)


def test_select_best_promotion_minimum_discount():
    assert promotion.select_best_promotion([promo(7, "TIME_SALE_FIXED", 1)], 1000, 1, NOW) == (7, 1)
    items = [promo(2, "BUNDLE", 1), promo(7, "TIME_SALE_FIXED", 1)]
    assert promotion.select_best_promotion(items, 1000, 1, NOW) == (7, 1)


def test_select_best_promotion_none():
    assert promotion.select_best_promotion([], 10000, 3, NOW) == (None, 0)
    assert promotion.select_best_promotion([promo(2, "BUNDLE", 1)], 10000, 1, NOW) == (None, 0)
    ended = promo(3, "TIME_SALE_RATE", 20, ends_at=NOW)
    assert promotion.select_best_promotion([ended], 10000, 3, NOW) == (None, 0)


@pytest.mark.parametrize("a,b,expected", [
    (promo(1, "BUNDLE", 1, stackable=True), promo(2, "N_PLUS_ONE", 2, stackable=True), True),
    (promo(1, "BUNDLE", 1, stackable=True), promo(1, "N_PLUS_ONE", 2, stackable=True), False),
    (promo(1, "BUNDLE", 1, stackable=True), promo(2, "BUNDLE", 1, stackable=True), False),
    (promo(1, "TIME_SALE_RATE", 10, stackable=True), promo(2, "BUNDLE", 1, stackable=True), False),
    (promo(1, "BUNDLE", 1, stackable=True), promo(2, "TIME_SALE_FIXED", 10, stackable=True), False),
    (promo(1, "BUNDLE", 1, stackable=False), promo(2, "N_PLUS_ONE", 2, stackable=True), False),
    (promo(1, "BUNDLE", 1, stackable=True), promo(2, "N_PLUS_ONE", 2, stackable=False), False),
    (promo(1, "BUNDLE", 1), promo(2, "N_PLUS_ONE", 2), False),
])
def test_can_combine(a, b, expected):
    assert promotion.can_combine(a, b) is expected


@pytest.mark.parametrize("total,sold,reserved,expected", [
    (100, 30, 10, 60),
    (100, 30, 0, 70),
    (100, 100, 0, 0),
    (100, 95, 10, 0),
    (100, 99, 0, 1),
    (100, 0, 0, 100),
])
def test_calc_remaining_stock(total, sold, reserved, expected):
    assert promotion.calc_remaining_stock(total, sold, reserved) == expected


def test_calc_remaining_stock_default_reserved():
    assert promotion.calc_remaining_stock(100, 30) == 70


@pytest.mark.parametrize("total,sold,reserved,expected", [
    (100, 100, 0, "SOLD_OUT"),
    (100, 95, 10, "SOLD_OUT"),
    (100, 90, 0, "LOW"),
    (100, 89, 0, "AVAILABLE"),
    (100, 99, 0, "LOW"),
    (100, 80, 15, "LOW"),
    (100, 80, 9, "AVAILABLE"),
    (100, 0, 0, "AVAILABLE"),
    (15, 14, 0, "LOW"),
    (15, 13, 0, "AVAILABLE"),
])
def test_get_stock_status(total, sold, reserved, expected):
    assert promotion.get_stock_status(total, sold, reserved) == expected


@pytest.fixture
def pconn(conn):
    promotion.ensure_promotion_schema(conn)
    return conn


def test_ensure_promotion_schema_idempotent(pconn):
    promotion.ensure_promotion_schema(pconn)
    rows = pconn.execute("SELECT name FROM sqlite_master WHERE name = 'promotion_purchases'").fetchall()
    assert len(rows) == 1


def test_record_and_get_purchased_quantity(pconn):
    assert promotion.get_purchased_quantity(pconn, 1, 10) == 0
    first = promotion.record_promo_purchase(pconn, 1, 10, 2, "2024-05-01 10:00:00")
    second = promotion.record_promo_purchase(pconn, 1, 10, 3, "2024-05-01 10:05:00")
    promotion.record_promo_purchase(pconn, 2, 10, 4, "2024-05-01 10:06:00")
    promotion.record_promo_purchase(pconn, 1, 11, 1, "2024-05-01 10:07:00")
    assert second == first + 1
    row = pconn.execute("SELECT promotion_id, customer_id, quantity, purchased_at "
                        "FROM promotion_purchases WHERE id = ?", (first,)).fetchone()
    assert tuple(row) == (1, 10, 2, "2024-05-01 10:00:00")
    assert promotion.get_purchased_quantity(pconn, 1, 10) == 5
    assert promotion.get_purchased_quantity(pconn, 2, 10) == 4
    assert promotion.get_purchased_quantity(pconn, 1, 11) == 1


def test_record_promo_purchase_limit(pconn):
    promotion.record_promo_purchase(pconn, 1, 10, 4, "2024-05-01 10:00:00")
    with pytest.raises(ValueError, match="4\\+2 > 5"):
        promotion.record_promo_purchase(pconn, 1, 10, 2, "2024-05-01 10:01:00")
    assert promotion.get_purchased_quantity(pconn, 1, 10) == 4
    promotion.record_promo_purchase(pconn, 1, 10, 1, "2024-05-01 10:02:00")
    assert promotion.get_purchased_quantity(pconn, 1, 10) == 5


def test_record_promo_purchase_custom_limit(pconn):
    promotion.record_promo_purchase(pconn, 1, 10, 2, "2024-05-01 10:00:00", limit=2)
    with pytest.raises(ValueError, match="한도"):
        promotion.record_promo_purchase(pconn, 1, 10, 1, "2024-05-01 10:01:00", limit=2)
    with pytest.raises(ValueError, match="수량"):
        promotion.record_promo_purchase(pconn, 2, 10, 0, "2024-05-01 10:01:00")
    assert pconn.execute("SELECT COUNT(*) FROM promotion_purchases").fetchone()[0] == 1
