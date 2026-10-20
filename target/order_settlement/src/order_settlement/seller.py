# -*- coding: utf-8 -*-
"""판매자 관리: 등급 산정, 정산 보류, 휴면 판단, 상품 등록 한도, 패널티·제재, 출고 지연율, 사업자 검증, 수수료 우대."""
from typing import Dict, List, Optional

from order_settlement import date_utils, db

NEW_SELLER_DAYS = 90               # 가입 후 신규 판매자로 보는 기간(일)
DORMANT_DAYS = 180                 # 최근 판매일 기준 휴면 판단 기간(일)
SHIP_DEADLINE_DAYS = 3             # 주문일 기준 출고 기한(일)
MIN_ORDERS_FOR_CLAIM = 20          # 클레임 비율 판단 최소 주문 수
HOLD_CLAIM_RATE = 50               # 정산 보류 클레임 비율(천분율)

PREMIUM_MIN_SALES = 50000000       # 프리미엄 등급 월 매출(원)
PREMIUM_MAX_CANCEL_RATE = 20       # 프리미엄 등급 취소율 상한(천분율)
EXCELLENT_MIN_SALES = 10000000     # 우수 등급 월 매출(원)
EXCELLENT_MAX_CANCEL_RATE = 30     # 우수 등급 취소율 상한(천분율)

PRODUCT_LIMITS = {"PREMIUM": 10000, "EXCELLENT": 3000, "GENERAL": 1000, "NEW": 100}

PENALTY_POINTS = {"LATE_SHIPMENT": 1, "UNFULFILLED": 3, "FALSE_INFO": 5, "FRAUD": 50}
PENALTY_DECAY_MONTHS = 6           # 무위반 기간이 이 개월 이상이면 기존 벌점 절반 감경
WARNING_POINTS = 10
SUSPEND_POINTS = 30
TERMINATE_POINTS = 50
BASE_SUSPEND_DAYS = 7
MAX_SUSPEND_DAYS = 30
SUSPEND_STEP_POINTS = 3           # 정지 기준 초과 벌점 단위
SUSPEND_STEP_DAYS = 5             # 단위당 추가 정지 일수

BASE_COMMISSION_RATE = 120         # 기본 판매 수수료(천분율, 12%)
MIN_COMMISSION_RATE = 85           # 수수료 하한(천분율)
PREFERRED_DISCOUNTS = {"PREMIUM": 30, "EXCELLENT": 15}   # 등급별 우대(천분율 차감)
SMALL_BIZ_DISCOUNT = 10            # 영세 사업자 추가 우대(천분율)

REQUIRED_BIZ_FIELDS = ["business_no", "company_name", "representative", "address"]
BIZ_NO_WEIGHTS = [1, 3, 7, 1, 3, 7, 1, 3, 5]

SELLER_SCHEMA = """
CREATE TABLE IF NOT EXISTS sellers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    business_no TEXT NOT NULL,
    grade TEXT NOT NULL DEFAULT 'NEW',
    joined_at TEXT,
    last_sale_at TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
);
"""


def calc_rate_permille(count, total):
    # type: (int, int) -> int
    """count / total 비율(천분율, 내림). total이 0 이하면 0."""
    if total <= 0:
        return 0
    return count * 1000 // total


def calc_seller_grade(monthly_sales, cancel_rate, days_active):
    # type: (int, int, int) -> str
    """월 매출(원)·취소율(천분율)·활동 일수로 판매자 등급 산정."""
    if days_active < NEW_SELLER_DAYS:
        return "NEW"
    if monthly_sales >= PREMIUM_MIN_SALES and cancel_rate <= PREMIUM_MAX_CANCEL_RATE:
        return "PREMIUM"
    if monthly_sales >= EXCELLENT_MIN_SALES and cancel_rate <= EXCELLENT_MAX_CANCEL_RATE:
        return "EXCELLENT"
    return "GENERAL"


def get_product_limit(grade):
    limit = PRODUCT_LIMITS.get(grade)
    if limit is None:
        raise ValueError("알 수 없는 판매자 등급: %s" % grade)
    return limit


def can_register_product(grade, current_count, adding=1):
    """등급별 상품 등록 한도 안에서 adding개를 더 등록할 수 있는지."""
    if adding <= 0:
        raise ValueError("등록 수량 오류: %d" % adding)
    return current_count + adding <= get_product_limit(grade)


def is_new_seller(joined_date, today):
    return date_utils.days_between(joined_date, today) < NEW_SELLER_DAYS


def decide_settlement_hold(joined_date, today, claim_count, order_count, sanction="NONE"):
    # type: (object, object, int, int, str) -> Optional[str]
    """정산 보류 사유. 보류 대상이 아니면 None."""
    if sanction in ("SUSPEND", "TERMINATE"):
        return "SANCTIONED"
    if is_new_seller(joined_date, today):
        return "NEW_SELLER"
    if order_count >= MIN_ORDERS_FOR_CLAIM and calc_rate_permille(claim_count, order_count) >= HOLD_CLAIM_RATE:
        return "HIGH_CLAIM"
    return None


def is_dormant_seller(last_sale_date, today):
    """최근 판매일로부터 DORMANT_DAYS일 이상 지나면 휴면. 판매 이력이 없으면 휴면."""
    if last_sale_date is None:
        return True
    return date_utils.days_between(last_sale_date, today) >= DORMANT_DAYS


def calc_penalty_points(violations):
    # type: (List[str]) -> int
    total = 0
    for v in violations:
        if v not in PENALTY_POINTS:
            raise ValueError("알 수 없는 위반 유형: %s" % v)
        total += PENALTY_POINTS[v]
    return total


def calc_total_penalty(prev_points, new_points, clean_months):
    """누적 벌점. 무위반 PENALTY_DECAY_MONTHS개월 이상이면 기존 벌점 절반(내림) 감경."""
    if clean_months >= PENALTY_DECAY_MONTHS:
        prev_points = prev_points // 2
    return prev_points + new_points


def get_sanction_level(points):
    if points >= TERMINATE_POINTS:
        return "TERMINATE"
    if points >= SUSPEND_POINTS:
        return "SUSPEND"
    if points >= WARNING_POINTS:
        return "WARNING"
    return "NONE"


def calc_suspend_days(points):
    """판매 정지 일수. 기본 7일, 정지 기준 초과 3점마다 5일 추가, 최대 30일. 퇴점이면 -1."""
    level = get_sanction_level(points)
    if level == "TERMINATE":
        return -1
    if level != "SUSPEND":
        return 0
    days = BASE_SUSPEND_DAYS + (points - SUSPEND_POINTS) // SUSPEND_STEP_POINTS * SUSPEND_STEP_DAYS
    return min(days, MAX_SUSPEND_DAYS)


def is_shipment_delayed(order_date, shipped_date, today):
    """출고 기한(주문일 + SHIP_DEADLINE_DAYS) 초과 여부. 미출고면 today 기준."""
    end = shipped_date if shipped_date is not None else today
    return date_utils.days_between(order_date, end) > SHIP_DEADLINE_DAYS


def calc_delay_rate(shipments, today):
    # type: (List[Dict], object) -> int
    """출고 지연율(천분율). shipments: [{"order_date": date, "shipped_date": date|None}]"""
    delayed = 0
    for s in shipments:
        if is_shipment_delayed(s["order_date"], s.get("shipped_date"), today):
            delayed += 1
    return calc_rate_permille(delayed, len(shipments))


def validate_business_no(business_no):
    """사업자등록번호 형식(10자리)과 검증번호 확인."""
    digits = (business_no or "").replace("-", "")
    if len(digits) != 10 or not digits.isdigit():
        return False
    nums = [int(c) for c in digits]
    total = sum(n * w for n, w in zip(nums[:9], BIZ_NO_WEIGHTS))
    total += nums[8] * 5 // 10
    check = (10 - total % 10) % 10
    return check == nums[9]


def validate_business_info(info):
    # type: (Dict[str, str]) -> bool
    missing = [f for f in REQUIRED_BIZ_FIELDS if not (info.get(f) or "").strip()]
    if missing:
        raise ValueError("사업자 필수값 누락: %s" % ", ".join(missing))
    if not validate_business_no(info["business_no"]):
        raise ValueError("사업자등록번호 오류")
    return True


def calc_commission_rate(grade, small_business=False):
    """판매 수수료율(천분율). 등급 우대·영세 사업자 우대 후 하한 적용."""
    rate = BASE_COMMISSION_RATE - PREFERRED_DISCOUNTS.get(grade, 0)
    if small_business:
        rate -= SMALL_BIZ_DISCOUNT
    return max(rate, MIN_COMMISSION_RATE)


def calc_commission(amount, grade, small_business=False):
    """판매 수수료(원, 내림)."""
    if amount < 0:
        raise ValueError("금액 오류: %d" % amount)
    return amount * calc_commission_rate(grade, small_business) // 1000


def ensure_seller_schema(conn):
    conn.executescript(SELLER_SCHEMA)
    conn.commit()


def insert_seller(conn, info, joined_at):
    """사업자 정보 검증 후 판매자 등록. 신규 판매자는 NEW 등급."""
    validate_business_info(info)
    return db.insert(conn, "sellers", {
        "company_name": info["company_name"],
        "business_no": info["business_no"],
        "grade": "NEW",
        "joined_at": joined_at,
        "status": "ACTIVE",
    })


def mark_dormant_sellers(conn, today):
    """ACTIVE 판매자 중 휴면 대상을 DORMANT로 바꾸고 id 목록을 돌려준다."""
    rows = db.query_all(conn, "SELECT id, last_sale_at FROM sellers WHERE status = 'ACTIVE' ORDER BY id")
    marked = []
    for row in rows:
        last = date_utils.parse_date(row["last_sale_at"]) if row["last_sale_at"] else None
        if is_dormant_seller(last, today):
            db.execute(conn, "UPDATE sellers SET status = 'DORMANT' WHERE id = ?", (row["id"],))
            marked.append(row["id"])
    return marked
