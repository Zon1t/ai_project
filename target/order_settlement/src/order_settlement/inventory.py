# -*- coding: utf-8 -*-
"""재고: 가용 재고(현재고-예약), 예약·해제·출고, 안전재고 경고, 발주량, 로트 입고·FIFO 출고 배분, 회전율·소진 예상일, 재고 실사 차이, ABC 분류."""
from datetime import timedelta
from typing import Dict, List, Optional

from order_settlement import db

LOT_PREFIX = "L"
LOT_LENGTH = 13                  # L + YYYYMMDD + - + 일련번호 3자리
REORDER_COVER_DAYS = 14          # 발주 시 확보할 추가 소요일
MIN_ORDER_UNIT = 10              # 발주 최소 단위(개)
VARIANCE_TOLERANCE_PERMIL = 20   # 실사 허용 오차(천분율)
ABC_A_PERCENT = 80
ABC_B_PERCENT = 95

INVENTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock (
    sku TEXT PRIMARY KEY,
    on_hand INTEGER NOT NULL DEFAULT 0,
    reserved INTEGER NOT NULL DEFAULT 0,
    safety_stock INTEGER NOT NULL DEFAULT 0
);
"""


def calc_available(on_hand, reserved):
    # type: (int, int) -> int
    """가용 재고 = 현재고 - 예약. 음수면 0."""
    return max(0, on_hand - reserved)


def can_fulfill(on_hand, reserved, qty):
    # type: (int, int, int) -> bool
    if qty <= 0:
        raise ValueError("수량 오류: %s" % qty)
    return calc_available(on_hand, reserved) >= qty


def reserve(stock, qty):
    # type: (Dict, int) -> Dict
    """주문 예약. 새 재고 dict를 돌려준다."""
    if not can_fulfill(stock["on_hand"], stock["reserved"], qty):
        raise ValueError("가용 재고 부족: 요청 %s" % qty)
    result = dict(stock)
    result["reserved"] = stock["reserved"] + qty
    return result


def release_reservation(stock, qty):
    # type: (Dict, int) -> Dict
    if qty <= 0:
        raise ValueError("수량 오류: %s" % qty)
    if qty > stock["reserved"]:
        raise ValueError("예약 수량 초과 해제: %s > %s" % (qty, stock["reserved"]))
    result = dict(stock)
    result["reserved"] = stock["reserved"] - qty
    return result


def ship_reserved(stock, qty):
    # type: (Dict, int) -> Dict
    """예약분 출고: 현재고와 예약을 함께 차감."""
    if qty <= 0:
        raise ValueError("수량 오류: %s" % qty)
    if qty > stock["reserved"] or qty > stock["on_hand"]:
        raise ValueError("출고 수량 초과: %s" % qty)
    result = dict(stock)
    result["on_hand"] = stock["on_hand"] - qty
    result["reserved"] = stock["reserved"] - qty
    return result


def check_safety_stock(available, safety_stock):
    # type: (int, int) -> str
    if available <= 0:
        return "OUT_OF_STOCK"
    if available < safety_stock // 2:
        return "CRITICAL"
    if available < safety_stock:
        return "LOW"
    return "OK"


def calc_reorder_qty(available, safety_stock, avg_daily_usage, lead_time_days):
    # type: (int, int, int, int) -> int
    """재주문점 이하이면 (재주문점 + 추가 소요분 - 가용) 을 발주 단위로 올림."""
    reorder_point = safety_stock + avg_daily_usage * lead_time_days
    if available > reorder_point:
        return 0
    target = reorder_point + avg_daily_usage * REORDER_COVER_DAYS
    qty = target - available
    return -(-qty // MIN_ORDER_UNIT) * MIN_ORDER_UNIT


def validate_lot_no(lot_no):
    # type: (str) -> bool
    """로트번호 형식: L20240603-001"""
    if len(lot_no) != LOT_LENGTH or not lot_no.startswith(LOT_PREFIX) or lot_no[9] != "-":
        return False
    ymd, seq = lot_no[1:9], lot_no[10:]
    if not ymd.isdigit() or not seq.isdigit() or seq == "000":
        return False
    month, day = int(ymd[4:6]), int(ymd[6:8])
    return 1 <= month <= 12 and 1 <= day <= 31


def receive_lot(lots, lot_no, qty, received_date):
    # type: (List[Dict], str, int, object) -> List[Dict]
    """입고 처리. 새 로트 목록을 돌려준다."""
    if qty <= 0:
        raise ValueError("입고 수량 오류: %s" % qty)
    if not validate_lot_no(lot_no):
        raise ValueError("로트번호 형식 오류: %s" % lot_no)
    if any(lot["lot_no"] == lot_no for lot in lots):
        raise ValueError("중복 로트: %s" % lot_no)
    return lots + [{"lot_no": lot_no, "qty": qty, "received_date": received_date}]


def allocate_fifo(lots, qty):
    # type: (List[Dict], int) -> List[tuple]
    """먼저 입고된 로트부터 배분. [(lot_no, 수량), ...]"""
    if qty <= 0:
        raise ValueError("수량 오류: %s" % qty)
    remaining = qty
    allocations = []
    for lot in sorted(lots, key=lambda x: (x["received_date"], x["lot_no"])):
        if remaining == 0:
            break
        take = min(lot["qty"], remaining)
        if take > 0:
            allocations.append((lot["lot_no"], take))
            remaining -= take
    if remaining > 0:
        raise ValueError("로트 재고 부족: %s개 부족" % remaining)
    return allocations


def calc_turnover_ratio(cogs, opening_value, closing_value):
    # type: (int, int, int) -> int
    """재고 회전율 x100 (매출원가 / 평균 재고금액)."""
    avg_value = (opening_value + closing_value) // 2
    if avg_value <= 0:
        return 0
    return cogs * 100 // avg_value


def calc_days_of_supply(available, avg_daily_usage):
    # type: (int, int) -> Optional[int]
    if avg_daily_usage <= 0:
        return None
    return available // avg_daily_usage


def estimate_stockout_date(today, available, avg_daily_usage):
    days = calc_days_of_supply(available, avg_daily_usage)
    if days is None:
        return None
    return today + timedelta(days=days)


def calc_count_variance(system_qty, counted_qty):
    # type: (int, int) -> Dict[str, int]
    """실사 차이와 차이율(천분율)."""
    diff = counted_qty - system_qty
    if system_qty > 0:
        rate = abs(diff) * 1000 // system_qty
    elif diff == 0:
        rate = 0
    else:
        rate = 1000
    return {"diff": diff, "rate": rate}


def classify_count_variance(system_qty, counted_qty):
    # type: (int, int) -> str
    variance = calc_count_variance(system_qty, counted_qty)
    if variance["diff"] == 0:
        return "MATCH"
    if variance["rate"] <= VARIANCE_TOLERANCE_PERMIL:
        return "MINOR"
    return "MAJOR"


def classify_abc(sku_values):
    # type: (Dict[str, int]) -> Dict[str, str]
    """금액 누적 비중 80% 이내 A, 95% 이내 B, 나머지 C."""
    total = sum(sku_values.values())
    result = {}
    cumulative = 0
    for sku, value in sorted(sku_values.items(), key=lambda x: (-x[1], x[0])):
        cumulative += value
        if cumulative * 100 <= total * ABC_A_PERCENT:
            result[sku] = "A"
        elif cumulative * 100 <= total * ABC_B_PERCENT:
            result[sku] = "B"
        else:
            result[sku] = "C"
    return result


def ensure_inventory_schema(conn):
    conn.executescript(INVENTORY_SCHEMA)
    conn.commit()


def upsert_stock(conn, sku, on_hand, safety_stock):
    """재고 등록 또는 현재고·안전재고 갱신 (예약은 유지)."""
    if on_hand < 0 or safety_stock < 0:
        raise ValueError("재고 수량 오류: %s" % sku)
    row = db.query_one(conn, "SELECT sku FROM stock WHERE sku = ?", (sku,))
    if row is None:
        db.insert(conn, "stock", {"sku": sku, "on_hand": on_hand, "reserved": 0, "safety_stock": safety_stock})
    else:
        db.execute(conn, "UPDATE stock SET on_hand = ?, safety_stock = ? WHERE sku = ?",
                   (on_hand, safety_stock, sku))


def reserve_stock(conn, sku, qty):
    # type: (object, str, int) -> int
    """DB 재고 예약 후 남은 가용 재고를 돌려준다."""
    row = db.query_one(conn, "SELECT * FROM stock WHERE sku = ?", (sku,))
    if row is None:
        raise ValueError("등록되지 않은 SKU: %s" % sku)
    updated = reserve(row, qty)
    db.execute(conn, "UPDATE stock SET reserved = ? WHERE sku = ?", (updated["reserved"], sku))
    return calc_available(updated["on_hand"], updated["reserved"])


def get_low_stock_skus(conn):
    # type: (object) -> List[str]
    """안전재고 미달(LOW 이하) SKU 목록, SKU 순."""
    rows = db.query_all(conn, "SELECT * FROM stock ORDER BY sku")
    result = []
    for row in rows:
        available = calc_available(row["on_hand"], row["reserved"])
        if check_safety_stock(available, row["safety_stock"]) != "OK":
            result.append(row["sku"])
    return result
