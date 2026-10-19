# -*- coding: utf-8 -*-
"""쿠폰 발급·검증."""
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from order_settlement import config, db

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def generate_coupon_code(customer_id, issued_at):
    raw = "%s-%s" % (customer_id, issued_at.strftime(DATE_FORMAT))
    return "CP" + hashlib.md5(raw.encode()).hexdigest()[:10].upper()


def create_coupon(conn, code, coupon_type, value, min_order=None, expires_at=None, customer_id=None):
    if coupon_type not in ("RATE", "FIXED"):
        raise ValueError("쿠폰 유형 오류: %s" % coupon_type)
    if coupon_type == "RATE" and not 0 < value <= 100:
        raise ValueError("정률 쿠폰 비율 오류: %s" % value)
    db.insert(conn, "coupons", {
        "code": code,
        "type": coupon_type,
        "value": value,
        "min_order": config.COUPON_MIN_ORDER if min_order is None else min_order,
        "expires_at": expires_at.strftime(DATE_FORMAT) if expires_at else None,
        "customer_id": customer_id,
    })
    return code


def find_coupon(conn, code):
    return db.query_one(conn, "SELECT * FROM coupons WHERE code = ?", (code,))


def is_expired(coupon, now=None):
    if not coupon.get("expires_at"):
        return False
    if now is None:
        now = datetime.utcnow()
    return now > datetime.strptime(coupon["expires_at"], DATE_FORMAT)


def validate_coupon(coupon, customer_id=None, now=None):
    # type: (Dict, object, object) -> Tuple[bool, str]
    if coupon is None:
        return False, "NOT_FOUND"
    if coupon["used"]:
        return False, "USED"
    if is_expired(coupon, now):
        return False, "EXPIRED"
    owner = coupon.get("customer_id")
    if owner is not None and customer_id is not None and owner != customer_id:
        return False, "NOT_OWNER"
    return True, "OK"


def issue_coupon(conn, customer_id, coupon_type, value, days_valid=30, now=None):
    """고객 전용 쿠폰 발급 (기본 30일 유효)."""
    if now is None:
        now = datetime.utcnow()
    code = generate_coupon_code(customer_id, now)
    create_coupon(conn, code, coupon_type, value,
                  expires_at=now + timedelta(days=days_valid), customer_id=customer_id)
    return code


def mark_used(conn, code):
    cur = db.execute(conn, "UPDATE coupons SET used = 1 WHERE code = ? AND used = 0", (code,))
    return cur.rowcount > 0


def list_customer_coupons(conn, customer_id, include_used=False):
    # type: (object, int, bool) -> List[Dict]
    if include_used:
        return db.query_all(conn, "SELECT * FROM coupons WHERE customer_id = ? ORDER BY code", (customer_id,))
    return db.query_all(conn, "SELECT * FROM coupons WHERE customer_id = ? AND used = 0 ORDER BY code",
                        (customer_id,))


def load_usable_coupons(conn, codes, customer_id=None, now=None):
    """코드 목록 중 사용 가능한 쿠폰만 순서대로 돌려준다."""
    result = []
    for code in codes:
        c = find_coupon(conn, code)
        ok, _ = validate_coupon(c, customer_id, now)
        if ok:
            result.append(c)
    return result
