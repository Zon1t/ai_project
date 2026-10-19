# -*- coding: utf-8 -*-
from datetime import datetime

from order_settlement import coupon, order, payment, report

CARD = "4111-1111-1111-1111"


def _sales(conn):
    o1 = order.create_order(conn, 1, created_at=datetime(2024, 3, 1))
    o2 = order.create_order(conn, 2, created_at=datetime(2024, 3, 1))
    o3 = order.create_order(conn, 2, created_at=datetime(2024, 3, 2))
    payment.process_payment(conn, o1, 50000, "CARD", CARD, now=datetime(2024, 3, 1, 10))
    payment.process_payment(conn, o2, 120000, "BANK", now=datetime(2024, 3, 1, 11))
    p3 = payment.process_payment(conn, o3, 30000, "CARD", CARD, now=datetime(2024, 3, 2, 9))
    payment.partial_refund(conn, p3, 10000)


def test_format_currency():
    assert report.format_currency(1234567) == "1,234,567원"
    assert report.format_currency(-1000) == "-1,000원"


def test_format_report_line():
    line = report.format_report_line("매출", 1000, width=6)
    assert line.startswith("매출    ")
    assert line.endswith("1,000원")


def test_daily_sales(seeded):
    _sales(seeded)
    assert report.daily_sales(seeded, "2024-03-01") == {"cnt": 2, "total": 170000}
    assert report.daily_sales(seeded, "2024-03-03") == {"cnt": 0, "total": 0}


def test_monthly_summary(seeded):
    _sales(seeded)
    s = report.monthly_summary(seeded, 2024, 3)
    # 수수료: 1,250 + 1,200 + 750
    assert s == {"month": "2024-03", "count": 3, "gross": 200000, "refunded": 10000, "fee": 3200, "net": 186800}


def test_top_customers(seeded):
    _sales(seeded)
    rows = report.top_customers(seeded, limit=1)
    assert [(r["id"], r["total"]) for r in rows] == [(2, 140000)]


def test_coupon_usage_report(conn):
    assert report.coupon_usage_report(conn) == {"issued": 0, "used": 0, "usage_rate": 0}
    for code in ("C1", "C2", "C3", "C4"):
        coupon.create_coupon(conn, code, "FIXED", 1000)
    coupon.mark_used(conn, "C1")
    assert report.coupon_usage_report(conn) == {"issued": 4, "used": 1, "usage_rate": 25}


def test_export_csv():
    out = report.export_csv([{"a": 1, "b": "x"}, {"a": 2}], ["a", "b"])
    assert out == "a,b\n1,x\n2,\n"


def test_build_customer_report(seeded):
    order.create_order(seeded, 3, created_at=datetime(2024, 3, 1))
    text = report.build_customer_report(seeded, 3)
    assert text.splitlines()[0].startswith("고객: 박지훈")
    assert "주문 수: 1" in text
