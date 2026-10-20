# -*- coding: utf-8 -*-
"""포인트(적립금): 등급별 적립, 사용 한도·단위 검증, 유효기간 만료·소멸 예정, 선입선출 차감, 환불 시 회수·복원."""
from datetime import timedelta
from typing import Any, Dict, List, Tuple

from order_settlement import db

POINT_EARN_RATES = {"BASIC": 1, "SILVER": 2, "GOLD": 3, "VIP": 5}   # 등급별 적립률(%)
MIN_EARN_AMOUNT = 10000        # 적립 대상 최소 결제 금액(원)
MAX_EARN_PER_ORDER = 5000      # 1회 적립 한도(포인트)
FIRST_ORDER_BONUS = 2000       # 첫 주문 보너스 포인트
POINT_USE_UNIT = 100           # 사용 단위(포인트)
MIN_POINT_USE = 1000           # 최소 사용 포인트
MAX_USE_RATE = 30              # 결제 금액 대비 사용 상한(%)
POINT_VALID_DAYS = 365         # 적립일로부터 유효기간(일)
EXPIRY_NOTICE_DAYS = 30        # 소멸 예정 안내 기준(일)

POINT_ENTRY_TYPES = ("EARN", "USE", "EXPIRE", "RECLAIM", "RESTORE")


def get_earn_rate(grade):
    # type: (str) -> int
    rate = POINT_EARN_RATES.get(grade)
    if rate is None:
        raise ValueError("알 수 없는 등급: %s" % grade)
    return rate


def calc_earn_points(paid_amount, grade):
    """결제 금액 기준 적립 포인트 (1회 한도 적용)."""
    if paid_amount < MIN_EARN_AMOUNT:
        return 0
    points = paid_amount * get_earn_rate(grade) // 100
    return min(points, MAX_EARN_PER_ORDER)


def calc_total_earn(paid_amount, grade, is_first_order):
    # type: (int, str, bool) -> int
    points = calc_earn_points(paid_amount, grade)
    if is_first_order and paid_amount >= MIN_EARN_AMOUNT:
        points += FIRST_ORDER_BONUS
    return points


def round_use_points(points):
    """사용 단위(100포인트) 미만 절사."""
    return points // POINT_USE_UNIT * POINT_USE_UNIT


def calc_max_usable(paid_amount, balance):
    """결제 금액 대비 상한과 보유 잔액 중 작은 값."""
    limit = paid_amount * MAX_USE_RATE // 100
    return round_use_points(min(limit, balance))


def validate_use_points(points, paid_amount, balance):
    # type: (int, int, int) -> bool
    if points < MIN_POINT_USE:
        raise ValueError("최소 사용 포인트 미만: %d" % points)
    if points % POINT_USE_UNIT != 0:
        raise ValueError("사용 단위 오류: %d" % points)
    if points > balance:
        raise ValueError("보유 포인트 부족: %d > %d" % (points, balance))
    if points > calc_max_usable(paid_amount, balance):
        raise ValueError("사용 상한 초과: %d" % points)
    return True


def calc_expiry_date(earned_on):
    return earned_on + timedelta(days=POINT_VALID_DAYS)


def is_expired(earned_on, today):
    """만료일 당일부터 소멸."""
    return today >= calc_expiry_date(earned_on)


def days_until_expiry(earned_on, today):
    # type: (Any, Any) -> int
    return (calc_expiry_date(earned_on) - today).days


def get_lot_status(lot, today):
    """적립 건 상태: USED / EXPIRED / PARTIAL / ACTIVE."""
    if lot["remaining"] <= 0:
        return "USED"
    if is_expired(lot["earned_on"], today):
        return "EXPIRED"
    if lot["remaining"] < lot["amount"]:
        return "PARTIAL"
    return "ACTIVE"


def calc_available_balance(lots, today):
    # type: (List[Dict[str, Any]], Any) -> int
    total = 0
    for lot in lots:
        if not is_expired(lot["earned_on"], today):
            total += lot["remaining"]
    return total


def calc_expiring_points(lots, today, within_days=EXPIRY_NOTICE_DAYS):
    """within_days 이내 소멸 예정 포인트 합계."""
    total = 0
    for lot in lots:
        if is_expired(lot["earned_on"], today):
            continue
        if days_until_expiry(lot["earned_on"], today) <= within_days:
            total += lot["remaining"]
    return total


def allocate_use_fifo(lots, points, today):
    # type: (List[Dict[str, Any]], int, Any) -> List[Tuple[int, int]]
    """오래된 적립분부터 차감. (lot 인덱스, 차감액) 목록."""
    order = sorted(range(len(lots)), key=lambda i: lots[i]["earned_on"])
    left = points
    allocations = []
    for idx in order:
        lot = lots[idx]
        if left == 0:
            break
        if lot["remaining"] <= 0 or is_expired(lot["earned_on"], today):
            continue
        used = min(lot["remaining"], left)
        allocations.append((idx, used))
        left -= used
    if left > 0:
        raise ValueError("사용 가능 포인트 부족: %d 부족" % left)
    return allocations


def calc_reclaim_points(earned_points, paid_amount, refund_amount):
    """환불 금액 비율만큼 적립 포인트 회수 (회수분은 올림)."""
    if refund_amount <= 0 or refund_amount > paid_amount:
        raise ValueError("환불 금액 오류: %d" % refund_amount)
    return (earned_points * refund_amount + paid_amount - 1) // paid_amount


def calc_restore_points(used_points, paid_amount, refund_amount):
    """환불 금액 비율만큼 사용 포인트 복원 (사용 단위 절사)."""
    if refund_amount <= 0 or refund_amount > paid_amount:
        raise ValueError("환불 금액 오류: %d" % refund_amount)
    if refund_amount == paid_amount:
        return used_points
    return round_use_points(used_points * refund_amount // paid_amount)


def apply_refund_to_balance(balance, earned_points, used_points, paid_amount, refund_amount):
    # type: (int, int, int, int, int) -> Dict[str, int]
    reclaim = calc_reclaim_points(earned_points, paid_amount, refund_amount)
    restore = calc_restore_points(used_points, paid_amount, refund_amount)
    new_balance = balance - reclaim + restore
    deficit = max(-new_balance, 0)       # 이미 사용해 회수 못 한 포인트
    return {"reclaim": reclaim, "restore": restore, "balance": max(new_balance, 0), "deficit": deficit}


def summarize_history(history):
    # type: (List[Dict[str, Any]]) -> Dict[str, int]
    summary = dict((t, 0) for t in POINT_ENTRY_TYPES)
    for entry in history:
        if entry["type"] not in summary:
            raise ValueError("알 수 없는 포인트 유형: %s" % entry["type"])
        summary[entry["type"]] += entry["amount"]
    plus = summary["EARN"] + summary["RESTORE"]
    minus = summary["USE"] + summary["EXPIRE"] + summary["RECLAIM"]
    summary["NET"] = plus - minus
    return summary


def ensure_point_schema(conn):
    db.execute(conn, """
        CREATE TABLE IF NOT EXISTS point_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            created_at TEXT
        )""")


def add_point_entry(conn, customer_id, entry_type, amount, created_at):
    if entry_type not in POINT_ENTRY_TYPES:
        raise ValueError("포인트 유형 오류: %s" % entry_type)
    if amount <= 0:
        raise ValueError("포인트 금액 오류: %d" % amount)
    return db.insert(conn, "point_ledger", {
        "customer_id": customer_id,
        "type": entry_type,
        "amount": amount,
        "created_at": created_at,
    })


def get_point_balance(conn, customer_id):
    # type: (Any, int) -> int
    rows = db.query_all(conn, "SELECT type, amount FROM point_ledger WHERE customer_id = ?",
                        (customer_id,))
    return summarize_history(rows)["NET"]
