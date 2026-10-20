# -*- coding: utf-8 -*-
"""배송: 지역별 배송비·도서산간 할증, 무게 구간 요금, 묶음배송, 운송장 번호 검증, 배송 상태 전이, 도착 예정일·지연 보상, 반품 배송비 부담."""
from typing import Dict, List, Optional

from order_settlement import config, date_utils, db

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

REGION_BASE_FEES = {"METRO": 3000, "LOCAL": 3500}   # 권역별 기본 배송비(원)
JEJU_ZIP_RANGE = (63000, 63644)                      # 제주 우편번호 구간
ISLAND_ZIP_RANGES = [(23100, 23116), (40200, 40240), (59102, 59166)]  # 도서산간
JEJU_SURCHARGE = 3000
ISLAND_SURCHARGE = 5000

WEIGHT_TIERS = [(2000, 0), (5000, 1000), (10000, 2500), (20000, 4000)]  # (상한 g, 추가요금)
MAX_PARCEL_WEIGHT_G = 20000

TRACKING_LENGTH = 12
TRACKING_CHECK_MOD = 7
MAX_TRACKING_SERIAL = 10 ** 11

STATUS_TRANSITIONS = {
    "READY": ["PICKED", "CANCELLED"],
    "PICKED": ["SHIPPED", "CANCELLED"],
    "SHIPPED": ["IN_TRANSIT", "RETURNED"],
    "IN_TRANSIT": ["DELIVERED", "RETURNED"],
    "DELIVERED": ["RETURNED"],
    "RETURNED": [],
    "CANCELLED": [],
}

SHIPPING_CUTOFF_HOUR = 14        # 이 시각 전 주문은 당일 출고
BASE_TRANSIT_DAYS = 1
EXTRA_TRANSIT_DAYS = {"JEJU": 1, "ISLAND": 2, "NORMAL": 0}

DELAY_COMP_MIN_DAYS = 2          # 이 일수 이상 지연 시 보상
DELAY_COMP_RATE_PER_DAY = 1      # 지연 1일당 주문금액의 %
DELAY_COMP_MAX_DAYS = 10

SELLER_FAULT_REASONS = ["DEFECT", "WRONG_ITEM", "DAMAGED", "LATE"]
BUYER_REASONS = ["CHANGE_MIND", "SIZE", "WRONG_ORDER"]
RETURN_BASE_FEE = 3000
RETURN_WINDOW_DAYS = 7

SHIPMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    tracking_no TEXT NOT NULL,
    zip_code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'READY',
    updated_at TEXT,
    delivered_at TEXT
);
"""


def get_region_base_fee(region):
    # type: (str) -> int
    fee = REGION_BASE_FEES.get(region)
    if fee is None:
        raise ValueError("알 수 없는 권역: %s" % region)
    return fee


def classify_remote_area(zip_code):
    # type: (str) -> str
    """우편번호로 JEJU / ISLAND / NORMAL 구분."""
    if len(zip_code) != 5 or not zip_code.isdigit():
        raise ValueError("우편번호 형식 오류: %s" % zip_code)
    z = int(zip_code)
    if JEJU_ZIP_RANGE[0] <= z <= JEJU_ZIP_RANGE[1]:
        return "JEJU"
    for low, high in ISLAND_ZIP_RANGES:
        if low <= z <= high:
            return "ISLAND"
    return "NORMAL"


def calc_remote_surcharge(zip_code):
    area = classify_remote_area(zip_code)
    if area == "JEJU":
        return JEJU_SURCHARGE
    if area == "ISLAND":
        return ISLAND_SURCHARGE
    return 0


def calc_weight_fee(weight_g):
    # type: (int) -> int
    """무게 구간별 추가 요금. 20kg 초과는 택배 불가."""
    if weight_g <= 0:
        raise ValueError("무게 오류: %s" % weight_g)
    if weight_g > MAX_PARCEL_WEIGHT_G:
        raise ValueError("택배 중량 초과: %sg" % weight_g)
    for limit, fee in WEIGHT_TIERS[:-1]:
        if weight_g <= limit:
            return fee
    return WEIGHT_TIERS[-1][1]


def calc_shipping_fee(order_amount, region, zip_code, weight_g):
    # type: (int, str, str, int) -> int
    """무료배송 기준 이상이면 기본 배송비만 면제. 무게·도서산간 요금은 별도."""
    if order_amount >= config.FREE_SHIPPING_THRESHOLD:
        base = 0
    else:
        base = get_region_base_fee(region)
    return base + calc_weight_fee(weight_g) + calc_remote_surcharge(zip_code)


def calc_bundle_shipping_fee(items):
    # type: (List[Dict]) -> int
    """판매자별 묶음배송. 판매자 단위로 무료배송 기준과 무게 요금을 적용."""
    groups = {}  # type: Dict[int, List[int]]
    for item in items:
        group = groups.setdefault(item["seller_id"], [0, 0])
        group[0] += item["amount"]
        group[1] += item["weight_g"]
    total = 0
    for amount, weight in groups.values():
        if amount < config.FREE_SHIPPING_THRESHOLD:
            total += config.SHIPPING_FEE
        total += calc_weight_fee(weight)
    return total


def validate_tracking_number(tracking_no):
    # type: (str) -> bool
    """운송장 번호 12자리, 앞 11자리 % 7 == 마지막 자리."""
    digits = tracking_no.replace("-", "")
    if len(digits) != TRACKING_LENGTH or not digits.isdigit():
        return False
    body = int(digits[:-1])
    return body % TRACKING_CHECK_MOD == int(digits[-1])


def make_tracking_number(serial):
    # type: (int) -> str
    if serial < 0 or serial >= MAX_TRACKING_SERIAL:
        raise ValueError("운송장 일련번호 범위 초과: %s" % serial)
    return "%011d%d" % (serial, serial % TRACKING_CHECK_MOD)


def format_tracking_number(tracking_no):
    """표시용 XXXX-XXXX-XXXX 형식."""
    if not validate_tracking_number(tracking_no):
        raise ValueError("운송장 번호 오류: %s" % tracking_no)
    digits = tracking_no.replace("-", "")
    return "%s-%s-%s" % (digits[:4], digits[4:8], digits[8:])


def can_transition(current, new):
    # type: (str, str) -> bool
    if current not in STATUS_TRANSITIONS:
        raise ValueError("알 수 없는 배송 상태: %s" % current)
    return new in STATUS_TRANSITIONS[current]


def calc_ship_date(order_date, order_hour):
    # type: (date, int) -> date
    """마감 시각 전·영업일 주문은 당일 출고, 그 외는 다음 영업일."""
    if order_hour < 0 or order_hour > 23:
        raise ValueError("주문 시각 오류: %s" % order_hour)
    if order_hour < SHIPPING_CUTOFF_HOUR and date_utils.is_business_day(order_date):
        return order_date
    return date_utils.add_business_days(order_date, 1)


def estimate_arrival_date(ship_date, zip_code):
    # type: (date, str) -> date
    area = classify_remote_area(zip_code)
    days = BASE_TRANSIT_DAYS + EXTRA_TRANSIT_DAYS[area]
    return date_utils.add_business_days(ship_date, days)


def is_delayed(expected_date, delivered_date, today):
    # type: (date, Optional[date], date) -> bool
    if delivered_date is not None:
        return delivered_date > expected_date
    return today > expected_date


def calc_delay_compensation(order_amount, expected_date, delivered_date):
    # type: (int, date, date) -> int
    """지연 보상액(10원 미만 절사). 최소 지연일 미만이면 0."""
    delay_days = (delivered_date - expected_date).days
    if delay_days < DELAY_COMP_MIN_DAYS:
        return 0
    days = min(delay_days, DELAY_COMP_MAX_DAYS)
    comp = order_amount * DELAY_COMP_RATE_PER_DAY * days // 100
    return comp // 10 * 10


def decide_return_fee_payer(reason):
    # type: (str) -> str
    if reason in SELLER_FAULT_REASONS:
        return "SELLER"
    if reason in BUYER_REASONS:
        return "BUYER"
    raise ValueError("알 수 없는 반품 사유: %s" % reason)


def calc_return_fee(reason, original_fee_paid, zip_code):
    # type: (str, int, str) -> int
    """구매자 귀책 반품비. 최초 배송이 무료였으면 왕복 배송비를 청구."""
    if decide_return_fee_payer(reason) == "SELLER":
        return 0
    fee = RETURN_BASE_FEE
    if original_fee_paid == 0:
        fee += config.SHIPPING_FEE
    return fee + calc_remote_surcharge(zip_code)


def is_return_window_open(delivered_date, request_date):
    # type: (date, date) -> bool
    elapsed = (request_date - delivered_date).days
    return 0 <= elapsed <= RETURN_WINDOW_DAYS


def ensure_shipment_schema(conn):
    conn.executescript(SHIPMENT_SCHEMA)
    conn.commit()


def create_shipment(conn, order_id, tracking_no, zip_code):
    # type: (object, int, str, str) -> int
    if not validate_tracking_number(tracking_no):
        raise ValueError("운송장 번호 오류: %s" % tracking_no)
    classify_remote_area(zip_code)
    return db.insert(conn, "shipments", {
        "order_id": order_id,
        "tracking_no": tracking_no.replace("-", ""),
        "zip_code": zip_code,
        "status": "READY",
    })


def update_shipment_status(conn, shipment_id, new_status, now):
    """상태 전이 규칙 검사 후 갱신. 배송완료 시 완료 시각 기록."""
    row = db.query_one(conn, "SELECT status FROM shipments WHERE id = ?", (shipment_id,))
    if row is None:
        raise ValueError("배송 정보 없음: %s" % shipment_id)
    if not can_transition(row["status"], new_status):
        raise ValueError("허용되지 않는 상태 전이: %s -> %s" % (row["status"], new_status))
    stamp = now.strftime(DATE_FORMAT)
    db.execute(conn, "UPDATE shipments SET status = ?, updated_at = ? WHERE id = ?",
               (new_status, stamp, shipment_id))
    if new_status == "DELIVERED":
        db.execute(conn, "UPDATE shipments SET delivered_at = ? WHERE id = ?", (stamp, shipment_id))
    return new_status
