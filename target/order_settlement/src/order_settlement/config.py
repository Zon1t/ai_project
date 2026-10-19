# -*- coding: utf-8 -*-
"""주문·정산 시스템 공통 설정.

2019년 최초 작성. 운영 중 값 변경은 운영팀 승인 필요.
"""
from datetime import date
from typing import Any, Dict, List

import pytz

TIMEZONE_NAME = "Asia/Seoul"

COUPON_MIN_ORDER = 100000          # 쿠폰 기본 최소 주문 금액(원)
FREE_SHIPPING_THRESHOLD = 50000    # 무료 배송 기준 금액(원)
SHIPPING_FEE = 3000                # 기본 배송비(원)
VAT_DIVISOR = 11                   # 부가세 포함 금액에서 부가세 = 금액 // 11
MAX_DISCOUNT_RATE = 50             # 등급 할인 상한(%)
SETTLEMENT_BUSINESS_DAYS = 5       # 월말 마감 후 지급까지 영업일 수

GRADE_ORDER = ["BASIC", "SILVER", "GOLD", "VIP"]
GRADE_DISCOUNT_RATES = {"BASIC": 0, "SILVER": 3, "GOLD": 5, "VIP": 7}
GRADE_THRESHOLDS = [("VIP", 3000000), ("GOLD", 1000000), ("SILVER", 300000)]
COMMISSION_RATES = {"BASIC": 12, "PARTNER": 10, "PREMIUM": 8}   # 판매자 수수료(%)
PG_FEE_RATES = {"CARD": 25, "BANK": 10, "POINT": 0}             # PG 수수료(천분율)

HOLIDAYS = [
    "2024-01-01", "2024-02-09", "2024-02-12", "2024-03-01", "2024-05-06",
    "2024-05-15", "2024-06-06", "2024-08-15", "2024-09-16", "2024-09-17",
    "2024-09-18", "2024-10-03", "2024-10-09", "2024-12-25",
    "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30", "2025-03-03",
    "2025-05-05", "2025-05-06", "2025-06-06", "2025-08-15", "2025-10-03",
    "2025-10-06", "2025-10-07", "2025-10-08", "2025-10-09", "2025-12-25",
]

_overrides = {}  # type: Dict[str, Any]


def get_setting(key, default=None):
    """설정값 조회. 런타임 오버라이드가 있으면 우선한다."""
    if key in _overrides:
        return _overrides[key]
    return globals().get(key, default)


def set_override(key, value):
    """런타임 오버라이드 지정. value가 None이면 해제한다."""
    if value is None:
        _overrides.pop(key, None)
    else:
        _overrides[key] = value


def get_timezone():
    return pytz.timezone(TIMEZONE_NAME)


def get_commission_rate(seller_grade):
    return COMMISSION_RATES.get(seller_grade, COMMISSION_RATES["BASIC"])


def get_holidays():
    # type: () -> List[date]
    return [date(*map(int, d.split("-"))) for d in HOLIDAYS]
