# -*- coding: utf-8 -*-
"""고객 서비스: 가입, 등급, 개인정보 마스킹."""
import logging
import re

from order_settlement import config, customer_dao

logger = logging.getLogger(__name__)

EMAIL_PATTERN = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
PHONE_PATTERN = re.compile(r"^01[016789]-?\d{3,4}-?\d{4}$")


def validate_email(email):
    return bool(email) and EMAIL_PATTERN.match(email) is not None


def validate_phone(phone):
    return bool(phone) and PHONE_PATTERN.match(phone) is not None


def normalize_phone(phone):
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11:
        return "%s-%s-%s" % (digits[:3], digits[3:7], digits[7:])
    if len(digits) == 10:
        return "%s-%s-%s" % (digits[:3], digits[3:6], digits[6:])
    raise ValueError("전화번호 형식 오류: %s" % phone)


def mask_name(name):
    if len(name) <= 1:
        return name
    if len(name) == 2:
        return name[0] + "*"
    return name[0] + "*" * (len(name) - 2) + name[-1]


def mask_phone(phone):
    parts = normalize_phone(phone).split("-")
    return "%s-****-%s" % (parts[0], parts[2])


def mask_email(email):
    local, domain = email.split("@", 1)
    return local[:2] + "*" * max(len(local) - 2, 1) + "@" + domain


def register_customer(conn, name, email, phone, created_at=None):
    """고객 가입. 중복 이메일은 거부한다."""
    logger.info("고객 등록 요청: name=%s, email=%s, phone=%s", name, email, phone)
    if not validate_email(email):
        raise ValueError("이메일 형식 오류: %s" % email)
    if not validate_phone(phone):
        raise ValueError("전화번호 형식 오류: %s" % phone)
    if customer_dao.find_by_email(conn, email):
        raise ValueError("이미 가입된 이메일: %s" % email)
    return customer_dao.insert_customer(conn, name, email, normalize_phone(phone), created_at=created_at)


def get_grade_discount_rate(grade):
    return config.GRADE_DISCOUNT_RATES.get(grade, 0)


def decide_grade(total_paid):
    """누적 결제 금액으로 등급 산정."""
    for grade, threshold in config.GRADE_THRESHOLDS:
        if total_paid >= threshold:
            return grade
    return "BASIC"


def upgrade_grade(conn, customer_id, total_paid):
    """등급 상향만 반영한다 (하향은 연 1회 배치에서 처리)."""
    customer = customer_dao.find_by_id(conn, customer_id)
    if customer is None:
        raise ValueError("고객 없음: id=%s" % customer_id)
    new_grade = decide_grade(total_paid)
    current = customer["grade"]
    if config.GRADE_ORDER.index(new_grade) > config.GRADE_ORDER.index(current):
        logger.info("등급 변경: %s(%s) %s -> %s", customer["name"], customer["phone"], current, new_grade)
        customer_dao.update_grade(conn, customer_id, new_grade)
        return new_grade
    return current


def customer_summary(conn, customer_id):
    """화면 표시용 고객 요약 (마스킹 적용)."""
    c = customer_dao.find_by_id(conn, customer_id)
    if c is None:
        return None
    return {
        "id": c["id"],
        "name": mask_name(c["name"]),
        "email": mask_email(c["email"]),
        "phone": mask_phone(c["phone"]),
        "grade": c["grade"],
    }
