# -*- coding: utf-8 -*-
"""세금·영수증: 부가세 공급가·세액 분리, 면세 혼합, 현금영수증·세금계산서, 사업자번호 검증, 간이과세, 신고 기한."""
import re
from datetime import date, datetime, timedelta
from typing import Dict, List

from order_settlement import date_utils, db

VAT_RATE = 10                          # 부가세율(%)
VAT_DIVISOR = 11                       # 부가세 포함 금액에서 세액 = 금액 // 11
TRUNCATE_UNIT = 10                     # 원 단위 절사 단위
CASH_RECEIPT_MIN_AMOUNT = 1            # 현금영수증 발급 최소 금액(원)
CASH_RECEIPT_MANDATORY_AMOUNT = 100000 # 의무발행 업종 의무발행 기준 금액(원)
CASH_METHODS = ["CASH", "BANK"]
SIMPLIFIED_EXEMPT_LIMIT = 48000000     # 간이과세자 납부면제 기준(연 공급대가)
SIMPLIFIED_LIMIT = 104000000           # 간이과세 적용 기준(연 공급대가)
VALUE_ADDED_RATE_MIN = 15              # 업종별 부가가치율 하한(%)
VALUE_ADDED_RATE_MAX = 40              # 업종별 부가가치율 상한(%)
FILING_DAY = 25                        # 신고 기한 일자 (다음 달)
LATE_PENALTY_RATE = 22                 # 납부지연가산세 1일 10만분의 22
BIZ_NUMBER_WEIGHTS = [1, 3, 7, 1, 3, 7, 1, 3, 5]


def truncate_won(amount, unit=TRUNCATE_UNIT):
    """원 단위 절사. 음수는 0 방향으로 절사한다."""
    if amount < 0:
        return -truncate_won(-amount, unit)
    return amount // unit * unit


def split_vat(total):
    # type: (int) -> Dict[str, int]
    """부가세 포함 금액을 공급가액과 세액으로 분리."""
    if total < 0:
        raise ValueError("음수 금액: %d" % total)
    vat = total // VAT_DIVISOR
    return {"supply": total - vat, "vat": vat}


def calc_vat_from_supply(supply):
    return supply * VAT_RATE // 100


def split_mixed_items(items):
    # type: (List[Dict]) -> Dict[str, int]
    """과세(TAXABLE)·면세(TAX_FREE) 상품 금액 합계 분리."""
    result = {"TAXABLE": 0, "TAX_FREE": 0}
    for item in items:
        tax_type = item.get("tax_type", "TAXABLE")
        if tax_type not in result:
            raise ValueError("알 수 없는 과세 유형: %s" % tax_type)
        result[tax_type] += item["price"] * item["quantity"]
    return result


def calc_mixed_receipt(items):
    # type: (List[Dict]) -> Dict[str, int]
    totals = split_mixed_items(items)
    taxable = split_vat(totals["TAXABLE"])
    return {
        "supply": taxable["supply"],
        "vat": taxable["vat"],
        "tax_free": totals["TAX_FREE"],
        "total": totals["TAXABLE"] + totals["TAX_FREE"],
    }


def is_cash_receipt_target(amount, method):
    return method in CASH_METHODS and amount >= CASH_RECEIPT_MIN_AMOUNT


def is_mandatory_cash_receipt(amount, method, mandatory_industry):
    """의무발행 업종의 10만원 이상 현금 거래는 요청이 없어도 발급."""
    if not mandatory_industry:
        return False
    return is_cash_receipt_target(amount, method) and amount >= CASH_RECEIPT_MANDATORY_AMOUNT


def decide_receipt_type(method, amount, is_business):
    if method == "CARD":
        return "CARD_SLIP"
    if is_business and amount > 0:
        return "TAX_INVOICE"
    if is_cash_receipt_target(amount, method):
        return "CASH_RECEIPT"
    return "NONE"


def validate_biz_number(text):
    """사업자등록번호 체크섬 검증 (하이픈 허용)."""
    digits = re.sub(r"-", "", text or "")
    if len(digits) != 10 or not digits.isdigit():
        return False
    nums = [int(c) for c in digits]
    total = 0
    for i in range(9):
        total += nums[i] * BIZ_NUMBER_WEIGHTS[i]
    total += nums[8] * 5 // 10
    check = (10 - total % 10) % 10
    return check == nums[9]


def validate_invoice_number(text):
    """전자세금계산서 승인번호 형식: YYYYMMDD-8자리-8자리."""
    parts = (text or "").split("-")
    if len(parts) != 3:
        return False
    for part in parts:
        if len(part) != 8 or not part.isdigit():
            return False
    try:
        datetime.strptime(parts[0], "%Y%m%d")
    except ValueError:
        return False
    return True


def classify_taxpayer(annual_sales):
    if annual_sales < 0:
        raise ValueError("음수 매출: %d" % annual_sales)
    if annual_sales < SIMPLIFIED_EXEMPT_LIMIT:
        return "SIMPLIFIED_EXEMPT"
    if annual_sales < SIMPLIFIED_LIMIT:
        return "SIMPLIFIED"
    return "GENERAL"


def calc_simplified_vat(sales, value_added_rate):
    """간이과세 세액 = 공급대가 x 업종별 부가가치율 x 10%, 10원 미만 절사."""
    if value_added_rate < VALUE_ADDED_RATE_MIN or value_added_rate > VALUE_ADDED_RATE_MAX:
        raise ValueError("부가가치율 범위 오류: %d" % value_added_rate)
    added = sales * value_added_rate // 100
    return truncate_won(added * VAT_RATE // 100)


def get_vat_period(month):
    if month < 1 or month > 12:
        raise ValueError("잘못된 월: %d" % month)
    if month <= 6:
        return "1H"
    return "2H"


def calc_filing_deadline(year, month):
    # type: (int, int) -> date
    """해당 월분 신고 기한: 다음 달 25일, 휴일이면 다음 영업일."""
    if month == 12:
        deadline = date(year + 1, 1, FILING_DAY)
    else:
        deadline = date(year, month + 1, FILING_DAY)
    while not date_utils.is_business_day(deadline):
        deadline += timedelta(days=1)
    return deadline


def calc_late_penalty(tax, days_late):
    """납부지연가산세 (1일 10만분의 22), 10원 미만 절사."""
    if tax <= 0 or days_late <= 0:
        return 0
    return truncate_won(tax * days_late * LATE_PENALTY_RATE // 100000)


def get_vat_return_status(output_vat, input_vat):
    # type: (int, int) -> Dict
    diff = output_vat - input_vat
    if diff > 0:
        return {"status": "PAY", "amount": diff}
    if diff < 0:
        return {"status": "REFUND", "amount": -diff}
    return {"status": "ZERO", "amount": 0}


def sum_monthly_tax(records):
    # type: (List[Dict]) -> Dict[str, int]
    """월별(YYYY-MM) 세액 합계. 취소(CANCELED) 건은 제외."""
    totals = {}  # type: Dict[str, int]
    for rec in records:
        if rec.get("status") == "CANCELED":
            continue
        key = rec["date"][:7]
        totals[key] = totals.get(key, 0) + rec["vat"]
    return dict(sorted(totals.items()))


def ensure_tax_schema(conn):
    db.execute(conn, """
        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            receipt_type TEXT NOT NULL,
            supply INTEGER NOT NULL,
            vat INTEGER NOT NULL,
            issued_at TEXT
        )""")


def record_receipt(conn, order_id, receipt_type, total, issued_at):
    if receipt_type not in ("TAX_INVOICE", "CASH_RECEIPT", "CARD_SLIP"):
        raise ValueError("발급 불가 영수증 유형: %s" % receipt_type)
    parts = split_vat(total)
    return db.insert(conn, "receipts", {
        "order_id": order_id, "receipt_type": receipt_type,
        "supply": parts["supply"], "vat": parts["vat"], "issued_at": issued_at,
    })


def sum_receipts_by_month(conn, year_month):
    # type: (object, str) -> Dict[str, int]
    row = db.query_one(
        conn,
        "SELECT COUNT(*) AS cnt, COALESCE(SUM(supply), 0) AS supply, COALESCE(SUM(vat), 0) AS vat"
        " FROM receipts WHERE substr(issued_at, 1, 7) = ?",
        (year_month,))
    return {"count": row["cnt"], "supply": row["supply"], "vat": row["vat"]}
