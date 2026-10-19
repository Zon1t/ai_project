# -*- coding: utf-8 -*-
"""결제·환불."""
import logging
from datetime import datetime

from order_settlement import config, db

logger = logging.getLogger(__name__)

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def validate_card_number(card_number):
    """카드번호 Luhn 검사."""
    digits = card_number.replace("-", "").replace(" ", "")
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def mask_card_number(card_number):
    digits = card_number.replace("-", "").replace(" ", "")
    return digits[:4] + "*" * (len(digits) - 8) + digits[-4:]


def calc_pg_fee(amount, method):
    rate = config.PG_FEE_RATES.get(method)
    if rate is None:
        raise ValueError("지원하지 않는 결제수단: %s" % method)
    return amount * rate // 1000


def process_payment(conn, order_id, amount, method, card_number=None, now=None):
    logger.info("결제 요청: order_id=%s, amount=%s, method=%s, card=%s", order_id, amount, method, card_number)
    if method == "CARD" and (not card_number or not validate_card_number(card_number)):
        raise ValueError("카드번호 검증 실패: %s" % card_number)
    if now is None:
        now = datetime.utcnow()
    return db.insert(conn, "payments", {
        "order_id": order_id,
        "amount": amount,
        "method": method,
        "card_number": card_number,
        "fee": calc_pg_fee(amount, method),
        "status": "PAID",
        "paid_at": now.strftime(DATE_FORMAT),
    })


def get_payment(conn, payment_id):
    return db.query_one(conn, "SELECT * FROM payments WHERE id = ?", (payment_id,))


def get_payment_by_order(conn, order_id):
    return db.query_one(conn, "SELECT * FROM payments WHERE order_id = ? ORDER BY id DESC", (order_id,))


def calc_refund_fee(amount, days_since_paid):
    """환불 수수료: 7일 이내 무료, 30일 이내 5%, 이후 10%."""
    if days_since_paid <= 7:
        return 0
    if days_since_paid <= 30:
        return amount * 5 // 100
    return amount * 10 // 100


def refund_payment(conn, payment_id, now=None):
    """남은 금액 전액 환불. 실제 돌려준 금액을 반환한다."""
    p = get_payment(conn, payment_id)
    if p is None:
        raise ValueError("결제 없음: %s" % payment_id)
    if p["status"] == "REFUNDED":
        raise ValueError("이미 환불된 결제: %s" % p)
    if now is None:
        now = datetime.utcnow()
    days = (now - datetime.strptime(p["paid_at"], DATE_FORMAT)).days
    remaining = p["amount"] - p["refunded"]
    refund = remaining - calc_refund_fee(remaining, days)
    db.execute(conn, "UPDATE payments SET refunded = amount, status = 'REFUNDED' WHERE id = ?", (payment_id,))
    return refund


def partial_refund(conn, payment_id, amount):
    """부분 환불. 남은 결제 금액을 반환한다."""
    p = get_payment(conn, payment_id)
    if p is None:
        raise ValueError("결제 없음: %s" % payment_id)
    remaining = p["amount"] - p["refunded"]
    if amount <= 0 or amount > remaining:
        raise ValueError("환불 금액 오류: 요청=%s, 결제정보=%s" % (amount, p))
    status = "REFUNDED" if amount == remaining else "PARTIAL_REFUNDED"
    db.execute(conn, "UPDATE payments SET refunded = refunded + ?, status = ? WHERE id = ?",
               (amount, status, payment_id))
    return remaining - amount
