# -*- coding: utf-8 -*-
"""상품 리뷰: 작성 가능 기간, 구매 확정, 평점 검증·평균·분포, 적립금, 금칙어, 베스트 리뷰, 신고 블라인드."""
from datetime import timedelta
from typing import Dict, List, Optional

from order_settlement import date_utils, db

REVIEW_PERIOD_DAYS = 30            # 배송완료 후 리뷰 작성 가능 기간(일)
EDIT_LIMIT_DAYS = 7                # 작성 후 수정 가능 기간(일)
MIN_RATING = 1
MAX_RATING = 5
MIN_TEXT_LENGTH = 20               # 최소 글자 수(공백 제외)
LONG_TEXT_LENGTH = 100             # 장문 리뷰 기준 글자 수
MAX_PHOTOS = 5
TEXT_REVIEW_POINT = 100            # 텍스트 리뷰 적립금(원)
PHOTO_REVIEW_POINT = 500           # 포토 리뷰 적립금(원)
LONG_TEXT_BONUS = 50               # 장문 추가 적립금(원)
FIRST_REVIEW_BONUS = 200           # 상품 첫 리뷰 추가 적립금(원)
BLIND_REPORT_COUNT = 5             # 신고 누적 시 블라인드 처리 기준
BEST_REVIEW_MIN_HELPFUL = 10       # 베스트 리뷰 최소 도움돼요 수
BEST_REVIEW_COUNT = 3
BANNED_WORDS = ["바보", "사기꾼", "쓰레기"]
CONFIRMED_STATUSES = ("CONFIRMED", "SETTLED")

REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    rating INTEGER NOT NULL,
    content TEXT NOT NULL,
    photo_count INTEGER NOT NULL DEFAULT 0,
    helpful INTEGER NOT NULL DEFAULT 0,
    report_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'VISIBLE',
    created_at TEXT
);
"""


def is_purchase_confirmed(order_status):
    # type: (str) -> bool
    """구매 확정(또는 정산 완료)된 주문인지."""
    if not order_status:
        return False
    return order_status.upper() in CONFIRMED_STATUSES


def calc_review_deadline(delivered_date):
    """리뷰 작성 마감일 (배송완료일 + REVIEW_PERIOD_DAYS, 당일 포함)."""
    if delivered_date is None:
        raise ValueError("배송완료일 없음")
    return delivered_date + timedelta(days=REVIEW_PERIOD_DAYS)


def is_review_period(delivered_date, today):
    if delivered_date is None:
        return False
    if today < delivered_date:
        return False
    return date_utils.days_between(delivered_date, today) <= REVIEW_PERIOD_DAYS


def can_write_review(order_status, delivered_date, today, already_written):
    """리뷰 작성 가능 여부를 사유 코드로 돌려준다."""
    if already_written:
        return "ALREADY_WRITTEN"
    if not is_purchase_confirmed(order_status):
        return "NOT_CONFIRMED"
    if not is_review_period(delivered_date, today):
        return "EXPIRED"
    return "OK"


def validate_rating(rating):
    # type: (int) -> int
    if isinstance(rating, bool) or not isinstance(rating, int):
        raise ValueError("평점은 정수여야 함: %s" % (rating,))
    if rating < MIN_RATING or rating > MAX_RATING:
        raise ValueError("평점 범위 오류: %d" % rating)
    return rating


def calc_average_rating(ratings):
    # type: (List[int]) -> float
    """평균 평점. 소수 둘째 자리에서 반올림(half-up)해 첫째 자리까지 표시.

    예: 4.05 -> 4.1, 4.04 -> 4.0. 리뷰가 없으면 0.0.
    """
    if not ratings:
        return 0.0
    total = sum(validate_rating(r) for r in ratings)
    count = len(ratings)
    scaled = (total * 20 + count) // (count * 2)
    return scaled / 10.0


def calc_rating_distribution(ratings):
    # type: (List[int]) -> Dict[int, int]
    """평점별 개수 {1: n, ..., 5: n}."""
    dist = {}  # type: Dict[int, int]
    for star in range(MIN_RATING, MAX_RATING + 1):
        dist[star] = 0
    for r in ratings:
        dist[validate_rating(r)] += 1
    return dist


def count_text_length(text):
    """공백(스페이스·개행·탭)을 제외한 글자 수."""
    if not text:
        return 0
    return len("".join(text.split()))


def is_text_long_enough(text):
    return count_text_length(text) >= MIN_TEXT_LENGTH


def mask_banned_words(text, banned=None):
    # type: (str, Optional[List[str]]) -> str
    """금칙어를 같은 길이의 '*'로 바꾼다."""
    if banned is None:
        banned = BANNED_WORDS
    masked = text
    for word in banned:
        if word:
            masked = masked.replace(word, "*" * len(word))
    return masked


def validate_photo_count(photo_count):
    if photo_count < 0:
        raise ValueError("사진 수 오류: %d" % photo_count)
    if photo_count > MAX_PHOTOS:
        raise ValueError("사진은 최대 %d장: %d" % (MAX_PHOTOS, photo_count))
    return photo_count


def calc_review_point(text, photo_count, is_first):
    """리뷰 적립금(원). 최소 글자 수 미달이면 0원.

    포토 리뷰 500원 / 텍스트 리뷰 100원, 장문 +50원, 상품 첫 리뷰 +200원.
    """
    validate_photo_count(photo_count)
    if not is_text_long_enough(text):
        return 0
    point = PHOTO_REVIEW_POINT if photo_count > 0 else TEXT_REVIEW_POINT
    if count_text_length(text) >= LONG_TEXT_LENGTH:
        point += LONG_TEXT_BONUS
    if is_first:
        point += FIRST_REVIEW_BONUS
    return point


def sort_reviews_by_helpful(reviews):
    # type: (List[Dict]) -> List[Dict]
    """도움돼요 많은 순, 같으면 최신 작성 순."""
    return sorted(reviews, key=lambda r: (r["helpful"], r["created_at"]), reverse=True)


def select_best_reviews(reviews, limit=BEST_REVIEW_COUNT):
    # type: (List[Dict], int) -> List[Dict]
    """블라인드가 아니고 도움돼요 BEST_REVIEW_MIN_HELPFUL 이상인 리뷰 중 상위 limit건."""
    candidates = [
        r for r in reviews
        if r.get("status", "VISIBLE") == "VISIBLE" and r["helpful"] >= BEST_REVIEW_MIN_HELPFUL
    ]
    return sort_reviews_by_helpful(candidates)[:limit]


def decide_review_status(report_count, current_status="VISIBLE"):
    """신고 누적 수에 따른 노출 상태. 삭제된 리뷰는 그대로 유지."""
    if current_status == "DELETED":
        return "DELETED"
    if report_count >= BLIND_REPORT_COUNT:
        return "BLIND"
    return "VISIBLE"


def can_edit_review(written_date, today, status):
    if status != "VISIBLE":
        return False
    elapsed = date_utils.days_between(written_date, today)
    return 0 <= elapsed <= EDIT_LIMIT_DAYS


def ensure_review_schema(conn):
    conn.executescript(REVIEW_SCHEMA)
    conn.commit()


def insert_review(conn, order_id, product_id, rating, content, photo_count, created_at):
    """리뷰 저장. 본문은 금칙어 마스킹 후 저장한다."""
    validate_rating(rating)
    validate_photo_count(photo_count)
    if not is_text_long_enough(content):
        raise ValueError("리뷰는 공백 제외 %d자 이상" % MIN_TEXT_LENGTH)
    return db.insert(conn, "reviews", {
        "order_id": order_id,
        "product_id": product_id,
        "rating": rating,
        "content": mask_banned_words(content),
        "photo_count": photo_count,
        "status": "VISIBLE",
        "created_at": created_at,
    })


def add_report(conn, review_id):
    """신고 1건 추가 후 바뀐 상태를 돌려준다."""
    row = db.query_one(conn, "SELECT report_count, status FROM reviews WHERE id = ?", (review_id,))
    if row is None:
        raise ValueError("리뷰 없음: %s" % review_id)
    count = row["report_count"] + 1
    status = decide_review_status(count, row["status"])
    db.execute(conn, "UPDATE reviews SET report_count = ?, status = ? WHERE id = ?", (count, status, review_id))
    return status


def get_product_rating_summary(conn, product_id):
    """노출 중인 리뷰 기준 상품 평점 요약."""
    rows = db.query_all(
        conn, "SELECT rating FROM reviews WHERE product_id = ? AND status = 'VISIBLE'", (product_id,))
    ratings = [r["rating"] for r in rows]
    return {
        "count": len(ratings),
        "average": calc_average_rating(ratings),
        "distribution": calc_rating_distribution(ratings),
    }
