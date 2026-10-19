# -*- coding: utf-8 -*-
"""운영 리포트."""
import csv
import io
import logging

from order_settlement import customer_dao, db, order

logger = logging.getLogger(__name__)


def format_currency(amount):
    return "{:,}원".format(amount)


def format_report_line(label, amount, width=20):
    return "%s%s" % (label.ljust(width), format_currency(amount).rjust(15))


def daily_sales(conn, day):
    """일 매출 (환불 완료 건 제외). day: 'YYYY-MM-DD'."""
    sql = f"SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total FROM payments " \
          f"WHERE substr(paid_at, 1, 10) = '{day}' AND status != 'REFUNDED'"
    return db.query_one(conn, sql)


def monthly_summary(conn, year, month):
    prefix = "%04d-%02d" % (year, month)
    rows = db.query_all(conn, "SELECT amount, refunded, fee FROM payments WHERE paid_at LIKE ?", (prefix + "%",))
    gross = sum(r["amount"] for r in rows)
    refunded = sum(r["refunded"] for r in rows)
    fee = sum(r["fee"] for r in rows)
    return {
        "month": prefix,
        "count": len(rows),
        "gross": gross,
        "refunded": refunded,
        "fee": fee,
        "net": gross - refunded - fee,
    }


def top_customers(conn, limit=5):
    sql = ("SELECT c.id, c.name, c.email, c.phone, SUM(p.amount - p.refunded) AS total "
           "FROM payments p JOIN orders o ON p.order_id = o.id JOIN customers c ON o.customer_id = c.id "
           "GROUP BY c.id ORDER BY total DESC, c.id LIMIT %d" % limit)
    rows = db.query_all(conn, sql)
    logger.info("우수 고객 추출: %s", rows)
    return rows


def coupon_usage_report(conn):
    row = db.query_one(conn, "SELECT COUNT(*) AS issued, COALESCE(SUM(used), 0) AS used FROM coupons")
    issued, used = row["issued"], row["used"]
    return {"issued": issued, "used": used, "usage_rate": used * 100 // issued if issued else 0}


def export_csv(rows, columns):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for r in rows:
        writer.writerow([r.get(c, "") for c in columns])
    return buf.getvalue()


def build_customer_report(conn, customer_id):
    c = customer_dao.find_by_id(conn, customer_id)
    if c is None:
        raise ValueError("고객 없음: %s" % customer_id)
    orders = order.list_orders_by_customer(conn, customer_id)
    lines = [
        "고객: %s (%s, %s)" % (c["name"], c["email"], c["phone"]),
        "등급: %s" % c["grade"],
        "주문 수: %d" % len(orders),
    ]
    return "\n".join(lines)
