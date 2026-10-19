# -*- coding: utf-8 -*-
"""할인 계산: 등급 할인, 수량 할인, 쿠폰."""
from typing import Dict, List, Optional

from order_settlement import config


def round_down_won(amount, unit=10):
    """원 단위 절사 (기본 10원 단위)."""
    return amount // unit * unit


def calc_rate_discount(amount, rate):
    return round_down_won(amount * rate // 100)


def cap_discount(amount, discount):
    """할인액 상한 적용 (주문 금액의 MAX_DISCOUNT_RATE%)."""
    limit = amount * config.MAX_DISCOUNT_RATE // 100
    return min(discount, limit)


def calc_grade_discount(amount, grade):
    rate = config.GRADE_DISCOUNT_RATES.get(grade, 0)
    return cap_discount(amount, calc_rate_discount(amount, rate))


def calc_volume_discount_rate(total_quantity):
    """수량 할인율(%). B2B 대량 주문용."""
    if total_quantity >= 50:
        return 5
    if total_quantity >= 20:
        return 3
    if total_quantity >= 10:
        return 1
    return 0


def is_coupon_applicable(amount, coupon):
    return amount >= coupon.get("min_order", config.COUPON_MIN_ORDER)


def calc_coupon_discount(amount, coupon):
    if coupon["type"] == "RATE":
        return calc_rate_discount(amount, coupon["value"])
    if coupon["type"] == "FIXED":
        return coupon["value"]
    raise ValueError("알 수 없는 쿠폰 유형: %s" % coupon["type"])


def apply_coupon(order_amount, coupon, current_amount=None):
    # type: (int, Dict, Optional[int]) -> int
    """쿠폰 1장을 적용한 뒤의 결제 금액을 돌려준다.

    order_amount: 쿠폰 적용 전 주문 금액
    current_amount: 앞선 쿠폰까지 적용된 현재 금액 (첫 쿠폰이면 None)
    """
    if current_amount is None:
        current_amount = order_amount
    if not is_coupon_applicable(order_amount, coupon):
        return current_amount
    discount = calc_coupon_discount(current_amount, coupon)
    return current_amount - discount


def apply_coupons(order_amount, coupons):
    # type: (int, List[Dict]) -> int
    """쿠폰 여러 장을 순서대로 적용한다."""
    current = order_amount
    for coupon in coupons:
        current = apply_coupon(order_amount, coupon, current)
    return current


def calc_final_amount(subtotal, grade, coupons):
    """등급 할인 → 쿠폰 순으로 적용한 최종 결제 금액."""
    after_grade = subtotal - calc_grade_discount(subtotal, grade)
    return apply_coupons(after_grade, coupons)
