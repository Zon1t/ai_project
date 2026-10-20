# -*- coding: utf-8 -*-
"""알림 발송: 템플릿 변수 치환, SMS/LMS 구분, 야간 발송 제한, 수신 동의별 채널 선택, 재시도, 일일 한도, 발송 비용."""
import re
from typing import Dict, List, Optional

from order_settlement import db

SMS_MAX_BYTES = 90                 # SMS 최대 바이트 (한글 2바이트)
LMS_MAX_BYTES = 2000               # LMS 최대 바이트
NIGHT_START_HOUR = 21              # 야간 시작 시각 (21시 포함)
NIGHT_END_HOUR = 8                 # 야간 종료 시각 (8시부터 발송 가능)
AD_PREFIX = "(광고)"
CHANNEL_PRIORITY = ["APP_PUSH", "ALIMTALK", "SMS"]
RETRY_BASE_SECONDS = 30            # 첫 재시도 대기(초)
RETRY_MAX_SECONDS = 600            # 재시도 대기 상한(초)
MAX_RETRY_COUNT = 3                # 최대 재시도 횟수
RETRYABLE_CODES = ["TIMEOUT", "RATE_LIMITED", "SERVER_ERROR"]
DAILY_SEND_LIMIT = 5               # 고객 1인당 1일 발송 한도
SEND_COSTS = {"APP_PUSH": 0, "ALIMTALK": 8, "SMS": 20, "LMS": 50}   # 건당 비용(원)
BULK_DISCOUNT_COUNT = 1000         # 대량 발송 할인 기준 건수
BULK_DISCOUNT_RATE = 10            # 대량 발송 할인율(%)

_VAR_PATTERN = re.compile(r"#\{(\w+)\}")


def find_template_vars(template):
    # type: (str) -> List[str]
    """템플릿에 쓰인 변수 이름 (등장 순서, 중복 제거)."""
    names = []  # type: List[str]
    for name in _VAR_PATTERN.findall(template):
        if name not in names:
            names.append(name)
    return names


def find_missing_vars(template, variables):
    # type: (str, Dict[str, object]) -> List[str]
    missing = []
    for name in find_template_vars(template):
        if name not in variables or variables[name] is None:
            missing.append(name)
    return missing


def render_template(template, variables):
    # type: (str, Dict[str, object]) -> str
    """#{변수} 치환. 누락 변수가 있으면 ValueError."""
    missing = find_missing_vars(template, variables)
    if missing:
        raise ValueError("템플릿 변수 누락: %s" % ", ".join(missing))
    result = template
    for name in find_template_vars(template):
        result = result.replace("#{%s}" % name, str(variables[name]))
    return result


def calc_message_bytes(text):
    """메시지 바이트 수. ASCII 1바이트, 그 외(한글 등) 2바이트."""
    total = 0
    for ch in text:
        if ord(ch) < 128:
            total += 1
        else:
            total += 2
    return total


def classify_message_type(text):
    size = calc_message_bytes(text)
    if size <= SMS_MAX_BYTES:
        return "SMS"
    if size <= LMS_MAX_BYTES:
        return "LMS"
    raise ValueError("메시지 길이 초과: %d바이트" % size)


def is_night_time(hour):
    if hour < 0 or hour > 23:
        raise ValueError("잘못된 시각: %d" % hour)
    return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR


def can_send_at(category, hour):
    """야간에는 광고성(AD) 메시지만 제한한다."""
    if category == "AD" and is_night_time(hour):
        return False
    return True


def hours_until_allowed(category, hour):
    # type: (str, int) -> int
    """발송 가능 시각까지 남은 시간(시간 단위). 지금 가능하면 0."""
    if can_send_at(category, hour):
        return 0
    if hour >= NIGHT_START_HOUR:
        return 24 - hour + NIGHT_END_HOUR
    return NIGHT_END_HOUR - hour


def add_ad_prefix(text, category):
    if category != "AD":
        return text
    if text.startswith(AD_PREFIX):
        return text
    return AD_PREFIX + " " + text


def select_channel(consents, app_installed, category="INFO"):
    # type: (Dict[str, bool], bool, str) -> Optional[str]
    """수신 동의와 앱 설치 여부로 채널 선택 (앱푸시→알림톡→SMS). 광고성은 알림톡 불가."""
    for channel in CHANNEL_PRIORITY:
        if not consents.get(channel, False):
            continue
        if channel == "APP_PUSH" and not app_installed:
            continue
        if channel == "ALIMTALK" and category == "AD":
            continue
        return channel
    return None


def calc_retry_delay(attempt):
    """attempt번째 재시도 대기(초). 지수 백오프, 상한 적용."""
    if attempt < 1:
        raise ValueError("attempt는 1 이상: %d" % attempt)
    delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
    return min(delay, RETRY_MAX_SECONDS)


def is_retryable(error_code):
    return error_code in RETRYABLE_CODES


def should_retry(error_code, attempt):
    """attempt: 지금까지 시도한 재시도 횟수."""
    if not is_retryable(error_code):
        return False
    return attempt < MAX_RETRY_COUNT


def check_daily_limit(sent_today, requested=1, category="INFO"):
    """1일 한도 확인. 거래성(INFO) 알림은 한도에서 제외."""
    if category != "AD":
        return True
    return sent_today + requested <= DAILY_SEND_LIMIT


def calc_send_cost(channel, text, count):
    # type: (str, str, int) -> int
    """발송 비용(원). SMS 채널은 길이에 따라 LMS 단가. 대량이면 할인."""
    if count <= 0:
        return 0
    if channel == "SMS":
        channel = classify_message_type(text)
    if channel not in SEND_COSTS:
        raise ValueError("알 수 없는 채널: %s" % channel)
    total = SEND_COSTS[channel] * count
    if count >= BULK_DISCOUNT_COUNT:
        total -= total * BULK_DISCOUNT_RATE // 100
    return total


def normalize_phone(phone):
    """휴대폰 번호 정규화 (숫자만, 010으로 시작하는 11자리)."""
    digits = re.sub(r"[^0-9]", "", phone or "")
    if len(digits) != 11 or not digits.startswith("010"):
        raise ValueError("잘못된 휴대폰 번호")
    return digits


def summarize_results(statuses):
    # type: (List[str]) -> Dict[str, int]
    """발송 결과 요약. success_rate는 백분율(정수, 절사)."""
    sent = len([s for s in statuses if s == "SENT"])
    failed = len([s for s in statuses if s == "FAILED"])
    total = len(statuses)
    rate = sent * 100 // total if total > 0 else 0
    return {"total": total, "sent": sent, "failed": failed, "success_rate": rate}


def ensure_notification_schema(conn):
    db.execute(conn, """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            category TEXT NOT NULL,
            status TEXT NOT NULL,
            sent_at TEXT
        )""")


def record_notification(conn, customer_id, channel, category, status, sent_at):
    if status not in ("SENT", "FAILED"):
        raise ValueError("알 수 없는 발송 상태: %s" % status)
    return db.insert(conn, "notifications", {
        "customer_id": customer_id, "channel": channel, "category": category,
        "status": status, "sent_at": sent_at,
    })


def count_sent_on(conn, customer_id, day, category="AD"):
    """해당 일자(YYYY-MM-DD)에 성공 발송한 건수."""
    row = db.query_one(
        conn,
        "SELECT COUNT(*) AS cnt FROM notifications WHERE customer_id = ? AND category = ?"
        " AND status = 'SENT' AND substr(sent_at, 1, 10) = ?",
        (customer_id, category, day))
    return row["cnt"]
