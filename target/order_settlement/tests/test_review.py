# -*- coding: utf-8 -*-
from datetime import date, timedelta

import pytest

from order_settlement import db, review

D = date(2024, 3, 1)
TEXT20 = "가" * 20
TEXT19 = "가" * 19


@pytest.mark.parametrize("status,expected", [
    ("CONFIRMED", True), ("SETTLED", True), ("confirmed", True), ("DELIVERED", False),
    ("CREATED", False), ("", False), (None, False),
])
def test_is_purchase_confirmed(status, expected):
    assert review.is_purchase_confirmed(status) is expected


def test_calc_review_deadline():
    assert review.calc_review_deadline(D) == date(2024, 3, 31)
    with pytest.raises(ValueError):
        review.calc_review_deadline(None)


@pytest.mark.parametrize("offset,expected", [(-1, False), (0, True), (1, True), (29, True), (30, True), (31, False)])
def test_is_review_period(offset, expected):
    assert review.is_review_period(D, D + timedelta(days=offset)) is expected


def test_is_review_period_no_delivery():
    assert review.is_review_period(None, D) is False


@pytest.mark.parametrize("status,delivered,offset,written,expected", [
    ("CONFIRMED", D, 0, False, "OK"),
    ("CONFIRMED", D, 30, False, "OK"),
    ("CONFIRMED", D, 31, False, "EXPIRED"),
    ("CONFIRMED", None, 0, False, "EXPIRED"),
    ("DELIVERED", D, 0, False, "NOT_CONFIRMED"),
    ("DELIVERED", D, 31, False, "NOT_CONFIRMED"),
    ("CONFIRMED", D, 0, True, "ALREADY_WRITTEN"),
    ("DELIVERED", D, 31, True, "ALREADY_WRITTEN"),
])
def test_can_write_review(status, delivered, offset, written, expected):
    assert review.can_write_review(status, delivered, D + timedelta(days=offset), written) == expected


@pytest.mark.parametrize("rating", [1, 2, 3, 4, 5])
def test_validate_rating_ok(rating):
    assert review.validate_rating(rating) == rating


@pytest.mark.parametrize("rating", [0, 6, -1, True, 3.0, "3", None])
def test_validate_rating_invalid(rating):
    with pytest.raises(ValueError):
        review.validate_rating(rating)


@pytest.mark.parametrize("ratings,expected", [
    ([], 0.0),
    ([4], 4.0),
    ([1, 2], 1.5),
    ([5, 5, 4], 4.7),
    ([1, 1, 2], 1.3),
    ([4] * 19 + [5], 4.1),           # 4.05 -> 4.1 (half-up)
    ([4] * 24 + [5], 4.0),           # 4.04 -> 4.0
    ([5] * 10, 5.0),
])
def test_calc_average_rating(ratings, expected):
    assert review.calc_average_rating(ratings) == expected


def test_calc_average_rating_invalid():
    with pytest.raises(ValueError):
        review.calc_average_rating([5, 6])


def test_calc_rating_distribution():
    assert review.calc_rating_distribution([5, 5, 3, 1]) == {1: 1, 2: 0, 3: 1, 4: 0, 5: 2}
    assert review.calc_rating_distribution([]) == {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    assert review.calc_rating_distribution([2, 4, 4]) == {1: 0, 2: 1, 3: 0, 4: 2, 5: 0}
    with pytest.raises(ValueError):
        review.calc_rating_distribution([0])


@pytest.mark.parametrize("text,expected", [
    ("", 0), (None, 0), ("abc", 3), ("a b\n c\t", 3), ("  좋아요  최고 ", 5),
])
def test_count_text_length(text, expected):
    assert review.count_text_length(text) == expected


@pytest.mark.parametrize("text,expected", [
    (TEXT19, False), (TEXT20, True), ("가 " * 20, True), ("가 " * 19, False), ("", False),
])
def test_is_text_long_enough(text, expected):
    assert review.is_text_long_enough(text) is expected


def test_mask_banned_words():
    assert review.mask_banned_words("이 바보 사기꾼아 쓰레기") == "이 ** ***아 ***"
    assert review.mask_banned_words("좋은 상품") == "좋은 상품"
    assert review.mask_banned_words("xabcx bad", ["abc", "", "bad"]) == "x***x ***"
    assert review.mask_banned_words("바보", []) == "바보"


@pytest.mark.parametrize("count", [0, 1, 5])
def test_validate_photo_count_ok(count):
    assert review.validate_photo_count(count) == count


@pytest.mark.parametrize("count", [-1, 6])
def test_validate_photo_count_invalid(count):
    with pytest.raises(ValueError):
        review.validate_photo_count(count)


@pytest.mark.parametrize("text,photos,first,expected", [
    (TEXT19, 1, True, 0),
    (TEXT20, 0, False, 100),
    (TEXT20, 1, False, 500),
    (TEXT20, 5, False, 500),
    ("가" * 99, 0, False, 100),
    ("가" * 100, 0, False, 150),
    ("가" * 100, 1, False, 550),
    (TEXT20, 0, True, 300),
    ("가" * 100, 5, True, 750),
])
def test_calc_review_point(text, photos, first, expected):
    assert review.calc_review_point(text, photos, first) == expected


def test_calc_review_point_invalid_photos():
    with pytest.raises(ValueError):
        review.calc_review_point(TEXT19, 6, False)
    with pytest.raises(ValueError):
        review.calc_review_point(TEXT20, -1, False)


def _r(rid, helpful, created, status="VISIBLE"):
    return {"id": rid, "helpful": helpful, "created_at": created, "status": status}


def test_sort_reviews_by_helpful():
    reviews = [_r(1, 5, "2024-03-01"), _r(2, 12, "2024-03-02"), _r(3, 5, "2024-03-05"), _r(4, 0, "2024-03-09")]
    assert [r["id"] for r in review.sort_reviews_by_helpful(reviews)] == [2, 3, 1, 4]
    assert review.sort_reviews_by_helpful([]) == []


def test_select_best_reviews():
    reviews = [
        _r(1, 9, "2024-03-01"),
        _r(2, 10, "2024-03-02"),
        _r(3, 50, "2024-03-03", "BLIND"),
        _r(4, 30, "2024-03-04"),
        _r(5, 11, "2024-03-05"),
        _r(6, 20, "2024-03-06"),
        {"id": 7, "helpful": 15, "created_at": "2024-03-07"},
    ]
    assert [r["id"] for r in review.select_best_reviews(reviews)] == [4, 6, 7]
    assert [r["id"] for r in review.select_best_reviews(reviews, 1)] == [4]
    assert [r["id"] for r in review.select_best_reviews(reviews, 10)] == [4, 6, 7, 5, 2]
    assert review.select_best_reviews([_r(1, 9, "2024-03-01")]) == []


@pytest.mark.parametrize("count,current,expected", [
    (0, "VISIBLE", "VISIBLE"), (4, "VISIBLE", "VISIBLE"), (5, "VISIBLE", "BLIND"), (6, "VISIBLE", "BLIND"),
    (10, "DELETED", "DELETED"), (0, "DELETED", "DELETED"), (5, "BLIND", "BLIND"),
])
def test_decide_review_status(count, current, expected):
    assert review.decide_review_status(count, current) == expected


def test_decide_review_status_default():
    assert review.decide_review_status(4) == "VISIBLE"
    assert review.decide_review_status(5) == "BLIND"


@pytest.mark.parametrize("offset,status,expected", [
    (-1, "VISIBLE", False), (0, "VISIBLE", True), (7, "VISIBLE", True), (8, "VISIBLE", False),
    (0, "BLIND", False), (0, "DELETED", False),
])
def test_can_edit_review(offset, status, expected):
    assert review.can_edit_review(D, D + timedelta(days=offset), status) is expected


@pytest.fixture
def rconn(conn):
    review.ensure_review_schema(conn)
    review.ensure_review_schema(conn)
    return conn


def test_insert_review(rconn):
    rid = review.insert_review(rconn, 10, 7, 4, "이 상품은 정말 좋아요 바보 같은 가격에 최고의 품질입니다", 2, "2024-03-01 10:00:00")
    assert rid == 1
    row = db.query_one(rconn, "SELECT * FROM reviews WHERE id = ?", (rid,))
    assert row["order_id"] == 10
    assert row["product_id"] == 7
    assert row["rating"] == 4
    assert row["content"] == "이 상품은 정말 좋아요 ** 같은 가격에 최고의 품질입니다"
    assert row["photo_count"] == 2
    assert row["helpful"] == 0
    assert row["report_count"] == 0
    assert row["status"] == "VISIBLE"
    assert row["created_at"] == "2024-03-01 10:00:00"


@pytest.mark.parametrize("rating,content,photos", [
    (6, TEXT20, 0), (0, TEXT20, 0), (5, TEXT19, 0), (5, TEXT20, 6), (5, TEXT20, -1),
])
def test_insert_review_invalid(rconn, rating, content, photos):
    with pytest.raises(ValueError):
        review.insert_review(rconn, 1, 1, rating, content, photos, "2024-03-01 10:00:00")
    assert db.query_all(rconn, "SELECT * FROM reviews") == []


def test_add_report(rconn):
    rid = review.insert_review(rconn, 1, 1, 5, TEXT20, 0, "2024-03-01 10:00:00")
    other = review.insert_review(rconn, 2, 1, 5, TEXT20, 0, "2024-03-01 10:00:00")
    assert [review.add_report(rconn, rid) for _ in range(4)] == ["VISIBLE"] * 4
    row = db.query_one(rconn, "SELECT report_count, status FROM reviews WHERE id = ?", (rid,))
    assert row == {"report_count": 4, "status": "VISIBLE"}
    assert review.add_report(rconn, rid) == "BLIND"
    assert review.add_report(rconn, rid) == "BLIND"
    row = db.query_one(rconn, "SELECT report_count, status FROM reviews WHERE id = ?", (rid,))
    assert row == {"report_count": 6, "status": "BLIND"}
    row = db.query_one(rconn, "SELECT report_count, status FROM reviews WHERE id = ?", (other,))
    assert row == {"report_count": 0, "status": "VISIBLE"}


def test_add_report_deleted(rconn):
    rid = review.insert_review(rconn, 1, 1, 5, TEXT20, 0, "2024-03-01 10:00:00")
    db.execute(rconn, "UPDATE reviews SET status = 'DELETED' WHERE id = ?", (rid,))
    assert review.add_report(rconn, rid) == "DELETED"


def test_add_report_missing(rconn):
    with pytest.raises(ValueError):
        review.add_report(rconn, 999)


def test_get_product_rating_summary(rconn):
    for rating in (5, 4, 4):
        review.insert_review(rconn, 1, 7, rating, TEXT20, 0, "2024-03-01 10:00:00")
    blind = review.insert_review(rconn, 1, 7, 1, TEXT20, 0, "2024-03-01 10:00:00")
    db.execute(rconn, "UPDATE reviews SET status = 'BLIND' WHERE id = ?", (blind,))
    review.insert_review(rconn, 1, 8, 1, TEXT20, 0, "2024-03-01 10:00:00")
    assert review.get_product_rating_summary(rconn, 7) == {
        "count": 3, "average": 4.3, "distribution": {1: 0, 2: 0, 3: 0, 4: 2, 5: 1},
    }
    assert review.get_product_rating_summary(rconn, 99) == {
        "count": 0, "average": 0.0, "distribution": {1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
    }
