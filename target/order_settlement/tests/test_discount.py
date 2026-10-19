# -*- coding: utf-8 -*-
import pytest

from order_settlement import discount


def fixed(value, min_order=100000):
    return {"type": "FIXED", "value": value, "min_order": min_order}


def rate(value, min_order=100000):
    return {"type": "RATE", "value": value, "min_order": min_order}


def test_round_down_won():
    assert discount.round_down_won(12345) == 12340
    assert discount.round_down_won(12345, 100) == 12300


def test_calc_rate_discount():
    assert discount.calc_rate_discount(12345, 10) == 1230
    assert discount.calc_rate_discount(100000, 0) == 0


def test_cap_discount():
    assert discount.cap_discount(100000, 60000) == 50000
    assert discount.cap_discount(100000, 3000) == 3000


def test_calc_grade_discount():
    assert discount.calc_grade_discount(100000, "GOLD") == 5000
    assert discount.calc_grade_discount(100000, "SILVER") == 3000
    assert discount.calc_grade_discount(100000, "BASIC") == 0


@pytest.mark.parametrize("qty,expected", [(9, 0), (10, 1), (19, 1), (20, 3), (49, 3), (50, 5)])
def test_volume_discount_rate(qty, expected):
    assert discount.calc_volume_discount_rate(qty) == expected


def test_is_coupon_applicable():
    assert discount.is_coupon_applicable(100000, fixed(5000)) is True
    assert discount.is_coupon_applicable(99999, fixed(5000)) is False
    assert discount.is_coupon_applicable(100000, {"type": "FIXED", "value": 5000}) is True
    assert discount.is_coupon_applicable(30000, fixed(5000, min_order=30000)) is True


def test_calc_coupon_discount():
    assert discount.calc_coupon_discount(123456, rate(10)) == 12340
    assert discount.calc_coupon_discount(123456, fixed(7000)) == 7000
    with pytest.raises(ValueError):
        discount.calc_coupon_discount(100000, {"type": "POINT", "value": 1})


def test_apply_coupon_fixed():
    assert discount.apply_coupon(150000, fixed(10000)) == 140000


def test_apply_coupon_rate_at_minimum():
    assert discount.apply_coupon(100000, rate(10)) == 90000


def test_apply_coupon_below_minimum():
    assert discount.apply_coupon(99000, fixed(10000)) == 99000


def test_apply_coupons_none():
    assert discount.apply_coupons(120000, []) == 120000


def test_apply_coupons_two_on_large_order():
    # 30만 원 주문: 정액 1만 원 → 29만 원, 정률 10% → 26만 1천 원
    assert discount.apply_coupons(300000, [fixed(10000), rate(10)]) == 261000


def test_calc_final_amount():
    # SILVER 3% 등급 할인 6,000원 → 194,000원, 쿠폰 1만 원 → 184,000원
    assert discount.calc_final_amount(200000, "SILVER", [fixed(10000)]) == 184000
    assert discount.calc_final_amount(80000, "BASIC", [fixed(10000)]) == 80000
