# -*- coding: utf-8 -*-
"""판매자 정산."""
from datetime import date, datetime
from typing import Tuple

import pytz

from order_settlement import config, coupon, customer_dao, date_utils, db, discount, order, payment


def calc_commission(amount, seller_grade="BASIC"):
    return amount * config.get_commission_rate(seller_grade) // 100


def calc_vat(amount):
    """부가세 포함 금액에서 부가세 추출."""
    return amount // config.VAT_DIVISOR


def calc_settlement_amount(subtotal, customer_grade, coupons, method="CARD", seller_grade="BASIC"):
    """판매자 정산 금액 = 결제 금액 - PG 수수료 - 판매 수수료."""
    paid = discount.calc_final_amount(subtotal, customer_grade, coupons)
    fee = payment.calc_pg_fee(paid, method)
    commission = calc_commission(paid, seller_grade)
    return paid - fee - commission


def calc_due_date(order_dt=None):
    """정산 지급일: 주문일(한국 시간) 기준 월말 마감 후 영업일 5일째.

    order_dt: UTC naive datetime. 없으면 현재 시각.
    """
    if order_dt is None:
        order_dt = datetime.utcnow()
    kst = pytz.timezone(config.TIMEZONE_NAME)
    local = pytz.utc.localize(order_dt).astimezone(kst)
    month_end = date(local.year, local.month, date_utils.last_day_of_month(local.year, local.month))
    return date_utils.add_business_days(month_end, config.SETTLEMENT_BUSINESS_DAYS)


def get_settlement_period(year, month):
    # type: (int, int) -> Tuple[date, date]
    return date(year, month, 1), date(year, month, date_utils.last_day_of_month(year, month))


def settle_order(conn, order_id, seller_grade="BASIC", method="CARD"):
    """결제 완료 주문의 정산 레코드 생성."""
    o = order.get_order(conn, order_id)
    if o is None:
        raise ValueError("주문 없음: %s" % order_id)
    if o["status"] not in ("PAID", "SHIPPED", "DELIVERED"):
        raise ValueError("정산 불가 상태: %s" % o["status"])
    customer = customer_dao.find_by_id(conn, o["customer_id"])
    coupons = [c for c in (coupon.find_coupon(conn, code) for code in o["coupon_codes"]) if c]
    subtotal = order.calc_subtotal(o["items"])
    paid = discount.calc_final_amount(subtotal, customer["grade"], coupons)
    amount = calc_settlement_amount(subtotal, customer["grade"], coupons, method, seller_grade)
    due = calc_due_date(datetime.strptime(o["created_at"], order.DATE_FORMAT))
    return db.insert(conn, "settlements", {
        "order_id": order_id,
        "amount": amount,
        "commission": calc_commission(paid, seller_grade),
        "due_date": date_utils.format_date(due),
        "status": "PENDING",
    })


def get_settlement(conn, settlement_id):
    return db.query_one(conn, "SELECT * FROM settlements WHERE id = ?", (settlement_id,))


def list_settlements(conn, status=None):
    if status is None:
        return db.query_all(conn, "SELECT * FROM settlements ORDER BY id")
    return db.query_all(conn, "SELECT * FROM settlements WHERE status = ? ORDER BY id", (status,))


def close_settlement(conn, settlement_id):
    s = get_settlement(conn, settlement_id)
    if s is None:
        raise ValueError("정산 없음: %s" % settlement_id)
    if s["status"] != "PENDING":
        raise ValueError("마감 불가: %s" % s)
    db.execute(conn, "UPDATE settlements SET status = 'CLOSED' WHERE id = ?", (settlement_id,))
    return True


def sum_settlements(conn, start, end):
    """지급일이 start~end(포함)인 정산 금액 합계."""
    row = db.query_one(
        conn,
        "SELECT COALESCE(SUM(amount), 0) AS total FROM settlements WHERE due_date BETWEEN ? AND ?",
        (date_utils.format_date(start), date_utils.format_date(end)),
    )
    return row["total"]


def is_settlement_overdue(settlement, today):
    if settlement["status"] != "PENDING":
        return False
    return date_utils.parse_date(settlement["due_date"]) < today
