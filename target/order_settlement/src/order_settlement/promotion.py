# -*- coding: utf-8 -*-
"""프로모션: 진행 기간, 타임세일가, N+1 증정, 묶음 할인, 카테고리 대상, 1인 구매 한도, 중복 불가·최대 혜택 선택, 선착순 재고."""
from typing import Any, Dict, List, Optional, Tuple

from order_settlement import db

PROMO_TYPES = ("TIME_SALE_RATE", "TIME_SALE_FIXED", "N_PLUS_ONE", "BUNDLE")
PROMO_PRIORITY = ["TIME_SALE_RATE", "TIME_SALE_FIXED", "BUNDLE", "N_PLUS_ONE"]   # 동일 혜택 시 우선순위
EXCLUSIVE_TYPES = ("TIME_SALE_RATE", "TIME_SALE_FIXED")                          # 다른 프로모션과 중복 불가
TIME_SALE_MAX_RATE = 70        # 타임세일 최대 할인율(%)
MIN_SALE_PRICE = 100           # 할인 후 최소 판매가(원)
MAX_PROMO_DISCOUNT = 50000     # 프로모션 1건 할인 상한(원)
MAX_PROMO_RATE = 40            # 주문 금액 대비 할인 상한(%)
DEFAULT_PURCHASE_LIMIT = 5     # 1인당 기본 구매 한도(개)
LOW_STOCK_RATE = 10            # 잔여 수량 임박 기준(%)


def get_promotion_status(promo, now):
    # type: (Dict[str, Any], Any) -> str
    if not promo.get("enabled", True):
        return "DISABLED"
    if now < promo["starts_at"]:
        return "SCHEDULED"
    if now >= promo["ends_at"]:
        return "ENDED"
    return "ACTIVE"


def is_promotion_active(promo, now):
    return get_promotion_status(promo, now) == "ACTIVE"


def validate_promotion(promo):
    """프로모션 등록 전 검증."""
    if promo["type"] not in PROMO_TYPES:
        raise ValueError("프로모션 유형 오류: %s" % promo["type"])
    if promo["starts_at"] >= promo["ends_at"]:
        raise ValueError("프로모션 기간 오류")
    if promo["value"] <= 0:
        raise ValueError("프로모션 값 오류: %s" % promo["value"])
    if promo["type"] == "TIME_SALE_RATE" and promo["value"] > TIME_SALE_MAX_RATE:
        raise ValueError("타임세일 할인율 초과: %s" % promo["value"])
    return True


def calc_time_sale_price(price, sale_type, value):
    """타임세일 판매가. 정률 할인액은 10원 단위 절사."""
    if sale_type == "TIME_SALE_RATE":
        discount = price * value // 100 // 10 * 10
    elif sale_type == "TIME_SALE_FIXED":
        discount = value
    else:
        raise ValueError("타임세일 유형 오류: %s" % sale_type)
    return max(price - discount, MIN_SALE_PRICE)


def calc_free_quantity(quantity, buy_n, free_m=1):
    # type: (int, int, int) -> int
    """N+M 증정 수량. (N+M)개 묶음마다 M개 무료."""
    if buy_n <= 0 or free_m <= 0:
        raise ValueError("N+M 조건 오류: %d+%d" % (buy_n, free_m))
    return quantity // (buy_n + free_m) * free_m


def calc_n_plus_one_discount(unit_price, quantity, buy_n, free_m=1):
    return unit_price * calc_free_quantity(quantity, buy_n, free_m)


def get_bundle_rate(quantity):
    """묶음 수량별 할인율(%)."""
    if quantity >= 5:
        return 15
    if quantity >= 3:
        return 10
    if quantity >= 2:
        return 5
    return 0


def calc_bundle_discount(items_total, quantity):
    # type: (int, int) -> int
    return items_total * get_bundle_rate(quantity) // 100


def is_category_eligible(category, promo):
    """제외 카테고리 우선, 대상 카테고리 미지정 시 전체 대상."""
    excluded = promo.get("excluded_categories") or []
    if category in excluded:
        return False
    included = promo.get("categories") or []
    if not included:
        return True
    return category in included


def filter_eligible_items(items, promo):
    # type: (List[Dict[str, Any]], Dict[str, Any]) -> List[Dict[str, Any]]
    return [item for item in items if is_category_eligible(item["category"], promo)]


def check_purchase_limit(quantity, already_bought, limit=DEFAULT_PURCHASE_LIMIT):
    """1인당 구매 한도 검사: OK / LIMIT_EXCEEDED."""
    if quantity <= 0:
        raise ValueError("구매 수량 오류: %d" % quantity)
    allowance = max(limit - already_bought, 0)
    if quantity > allowance:
        return "LIMIT_EXCEEDED"
    return "OK"


def cap_promo_discount(amount, discount, max_discount=MAX_PROMO_DISCOUNT):
    """할인 상한: 정액 상한과 주문 금액 비율 상한 중 작은 값."""
    cap = min(max_discount, amount * MAX_PROMO_RATE // 100)
    return min(discount, cap)


def calc_promo_discount(promo, unit_price, quantity):
    # type: (Dict[str, Any], int, int) -> int
    amount = unit_price * quantity
    promo_type = promo["type"]
    if promo_type in EXCLUSIVE_TYPES:
        sale_price = calc_time_sale_price(unit_price, promo_type, promo["value"])
        discount = (unit_price - sale_price) * quantity
    elif promo_type == "N_PLUS_ONE":
        discount = calc_n_plus_one_discount(unit_price, quantity, promo["value"])
    elif promo_type == "BUNDLE":
        discount = calc_bundle_discount(amount, quantity)
    else:
        raise ValueError("프로모션 유형 오류: %s" % promo_type)
    return cap_promo_discount(amount, discount)


def select_best_promotion(promos, unit_price, quantity, now):
    # type: (List[Dict[str, Any]], int, int, Any) -> Tuple[Optional[Any], int]
    """진행 중 프로모션 중 할인액 최대 1건 (중복 적용 불가)."""
    best_id, best_discount, best_rank = None, 0, len(PROMO_PRIORITY)
    for promo in promos:
        if not is_promotion_active(promo, now):
            continue
        discount = calc_promo_discount(promo, unit_price, quantity)
        if discount <= 0:
            continue
        rank = PROMO_PRIORITY.index(promo["type"])
        if discount > best_discount or (discount == best_discount and rank < best_rank):
            best_id, best_discount, best_rank = promo["id"], discount, rank
    return best_id, best_discount


def can_combine(promo_a, promo_b):
    """중복 적용 가능 여부. 타임세일은 단독, 같은 유형끼리도 불가."""
    if promo_a["id"] == promo_b["id"]:
        return False
    if promo_a["type"] in EXCLUSIVE_TYPES or promo_b["type"] in EXCLUSIVE_TYPES:
        return False
    if promo_a["type"] == promo_b["type"]:
        return False
    return bool(promo_a.get("stackable")) and bool(promo_b.get("stackable"))


def calc_remaining_stock(total_stock, sold, reserved=0):
    # type: (int, int, int) -> int
    return max(total_stock - sold - reserved, 0)


def get_stock_status(total_stock, sold, reserved=0):
    """선착순 잔여 상태: SOLD_OUT / LOW / AVAILABLE."""
    remaining = calc_remaining_stock(total_stock, sold, reserved)
    if remaining == 0:
        return "SOLD_OUT"
    if remaining * 100 <= total_stock * LOW_STOCK_RATE:
        return "LOW"
    return "AVAILABLE"


def ensure_promotion_schema(conn):
    db.execute(conn, """
        CREATE TABLE IF NOT EXISTS promotion_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promotion_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            purchased_at TEXT
        )""")


def record_promo_purchase(conn, promotion_id, customer_id, quantity, purchased_at,
                          limit=DEFAULT_PURCHASE_LIMIT):
    """한도 검사 후 구매 기록. 한도 초과 시 ValueError."""
    bought = get_purchased_quantity(conn, promotion_id, customer_id)
    if check_purchase_limit(quantity, bought, limit) != "OK":
        raise ValueError("1인 구매 한도 초과: %d+%d > %d" % (bought, quantity, limit))
    return db.insert(conn, "promotion_purchases", {
        "promotion_id": promotion_id,
        "customer_id": customer_id,
        "quantity": quantity,
        "purchased_at": purchased_at,
    })


def get_purchased_quantity(conn, promotion_id, customer_id):
    # type: (Any, int, int) -> int
    row = db.query_one(conn, "SELECT COALESCE(SUM(quantity), 0) AS qty FROM promotion_purchases "
                             "WHERE promotion_id = ? AND customer_id = ?", (promotion_id, customer_id))
    return row["qty"]
