# -*- coding: utf-8 -*-
from datetime import date

import pytest

from order_settlement import tax


@pytest.mark.parametrize("amount,unit,expected", [
    (12345, 10, 12340), (12340, 10, 12340), (9, 10, 0), (10, 10, 10), (0, 10, 0),
    (-12345, 10, -12340), (12345, 100, 12300), (-12345, 100, -12300), (-9, 10, 0),
])
def test_truncate_won(amount, unit, expected):
    assert tax.truncate_won(amount, unit) == expected


def test_truncate_won_default_unit():
    assert tax.truncate_won(12345) == 12340
    assert tax.truncate_won(-12345) == -12340


@pytest.mark.parametrize("total,supply,vat", [
    (11000, 10000, 1000), (10999, 10000, 999), (11011, 10010, 1001),
    (0, 0, 0), (10, 10, 0), (11, 10, 1), (21, 20, 1), (22, 20, 2),
])
def test_split_vat(total, supply, vat):
    assert tax.split_vat(total) == {"supply": supply, "vat": vat}


def test_split_vat_negative():
    with pytest.raises(ValueError):
        tax.split_vat(-1)


@pytest.mark.parametrize("supply,expected", [(10000, 1000), (12345, 1234), (9, 0), (10, 1), (0, 0)])
def test_calc_vat_from_supply(supply, expected):
    assert tax.calc_vat_from_supply(supply) == expected


def test_split_mixed_items():
    items = [
        {"price": 10000, "quantity": 2, "tax_type": "TAXABLE"},
        {"price": 3000, "quantity": 3, "tax_type": "TAX_FREE"},
        {"price": 500, "quantity": 4},
        {"price": 1000, "quantity": 1, "tax_type": "TAX_FREE"},
    ]
    assert tax.split_mixed_items(items) == {"TAXABLE": 22000, "TAX_FREE": 10000}
    assert tax.split_mixed_items([]) == {"TAXABLE": 0, "TAX_FREE": 0}


def test_split_mixed_items_unknown_type():
    with pytest.raises(ValueError):
        tax.split_mixed_items([{"price": 1000, "quantity": 1, "tax_type": "ZERO_RATED"}])


def test_calc_mixed_receipt():
    items = [
        {"price": 11000, "quantity": 2, "tax_type": "TAXABLE"},
        {"price": 5000, "quantity": 1, "tax_type": "TAX_FREE"},
    ]
    assert tax.calc_mixed_receipt(items) == {"supply": 20000, "vat": 2000, "tax_free": 5000, "total": 27000}
    assert tax.calc_mixed_receipt([{"price": 7000, "quantity": 1, "tax_type": "TAX_FREE"}]) == \
        {"supply": 0, "vat": 0, "tax_free": 7000, "total": 7000}


@pytest.mark.parametrize("amount,method,expected", [
    (1, "CASH", True), (0, "CASH", False), (1, "BANK", True), (50000, "BANK", True),
    (1000, "CARD", False), (1000, "POINT", False), (-100, "CASH", False),
])
def test_is_cash_receipt_target(amount, method, expected):
    assert tax.is_cash_receipt_target(amount, method) is expected


@pytest.mark.parametrize("amount,method,mandatory,expected", [
    (100000, "CASH", True, True), (99999, "CASH", True, False), (100001, "BANK", True, True),
    (100000, "CASH", False, False), (200000, "CARD", True, False), (0, "CASH", True, False),
])
def test_is_mandatory_cash_receipt(amount, method, mandatory, expected):
    assert tax.is_mandatory_cash_receipt(amount, method, mandatory) is expected


@pytest.mark.parametrize("method,amount,business,expected", [
    ("CARD", 10000, True, "CARD_SLIP"),
    ("CARD", 10000, False, "CARD_SLIP"),
    ("CASH", 10000, True, "TAX_INVOICE"),
    ("BANK", 1, True, "TAX_INVOICE"),
    ("POINT", 1, True, "TAX_INVOICE"),
    ("BANK", 0, True, "NONE"),
    ("CASH", 1, False, "CASH_RECEIPT"),
    ("BANK", 10000, False, "CASH_RECEIPT"),
    ("CASH", 0, False, "NONE"),
    ("POINT", 10000, False, "NONE"),
])
def test_decide_receipt_type(method, amount, business, expected):
    assert tax.decide_receipt_type(method, amount, business) == expected


@pytest.mark.parametrize("number", [
    "124-81-00998", "220-81-62517", "105-87-71311", "214-86-18758", "120-81-47521",
    "1234567891", "9876543215", "1111111119", "1357924681", "1234567080",
])
def test_validate_biz_number_valid(number):
    assert tax.validate_biz_number(number) is True


@pytest.mark.parametrize("number", [
    "124-81-00999", "124-81-00997", "220-81-62518", "1234567892", "1234567081", "1234567089",
    "123456789", "12345678912", "12345678a1", "", None, "124 81 00998",
])
def test_validate_biz_number_invalid(number):
    assert tax.validate_biz_number(number) is False


@pytest.mark.parametrize("text,expected", [
    ("20240315-12345678-87654321", True),
    ("20240229-00000000-00000000", True),
    ("20230229-12345678-87654321", False),
    ("20241301-12345678-87654321", False),
    ("2024031-12345678-87654321", False),
    ("202403150-12345678-87654321", False),
    ("20240315-1234567-87654321", False),
    ("20240315-12345678-876543210", False),
    ("20240315-1234567a-87654321", False),
    ("20240315-12345678", False),
    ("20240315-12345678-87654321-1", False),
    ("", False),
    (None, False),
])
def test_validate_invoice_number(text, expected):
    assert tax.validate_invoice_number(text) is expected


@pytest.mark.parametrize("sales,expected", [
    (0, "SIMPLIFIED_EXEMPT"), (47999999, "SIMPLIFIED_EXEMPT"), (48000000, "SIMPLIFIED"),
    (103999999, "SIMPLIFIED"), (104000000, "GENERAL"), (500000000, "GENERAL"),
])
def test_classify_taxpayer(sales, expected):
    assert tax.classify_taxpayer(sales) == expected


def test_classify_taxpayer_negative():
    with pytest.raises(ValueError):
        tax.classify_taxpayer(-1)


@pytest.mark.parametrize("sales,rate,expected", [
    (10000000, 15, 150000), (1234567, 15, 18510), (1234567, 40, 49380),
    (1234567, 20, 24690), (0, 30, 0),
])
def test_calc_simplified_vat(sales, rate, expected):
    assert tax.calc_simplified_vat(sales, rate) == expected


@pytest.mark.parametrize("rate", [14, 41, 0, 100])
def test_calc_simplified_vat_rate_out_of_range(rate):
    with pytest.raises(ValueError):
        tax.calc_simplified_vat(1000000, rate)


@pytest.mark.parametrize("month,expected", [(1, "1H"), (6, "1H"), (7, "2H"), (12, "2H")])
def test_get_vat_period(month, expected):
    assert tax.get_vat_period(month) == expected


@pytest.mark.parametrize("month", [0, 13, -1])
def test_get_vat_period_invalid(month):
    with pytest.raises(ValueError):
        tax.get_vat_period(month)


@pytest.mark.parametrize("year,month,expected", [
    (2024, 2, date(2024, 3, 25)),     # 월요일
    (2024, 3, date(2024, 4, 25)),     # 목요일
    (2024, 1, date(2024, 2, 26)),     # 2/25 일요일
    (2025, 9, date(2025, 10, 27)),    # 10/25 토요일
    (2024, 11, date(2024, 12, 26)),   # 12/25 공휴일
    (2024, 12, date(2025, 1, 27)),    # 연도 넘김, 1/25 토요일
    (2023, 12, date(2024, 1, 25)),    # 연도 넘김, 목요일
])
def test_calc_filing_deadline(year, month, expected):
    assert tax.calc_filing_deadline(year, month) == expected


@pytest.mark.parametrize("amount,days,expected", [
    (1000000, 10, 2200), (1000000, 1, 220), (1234567, 3, 810), (100000, 4, 80),
    (100000, 5, 110), (1000000, 0, 0), (1000000, -3, 0), (0, 10, 0), (-1000000, 10, 0), (1, 1, 0),
])
def test_calc_late_penalty(amount, days, expected):
    assert tax.calc_late_penalty(amount, days) == expected


@pytest.mark.parametrize("output_vat,input_vat,expected", [
    (1000, 400, {"status": "PAY", "amount": 600}),
    (400, 1000, {"status": "REFUND", "amount": 600}),
    (1000, 1000, {"status": "ZERO", "amount": 0}),
    (1001, 1000, {"status": "PAY", "amount": 1}),
    (999, 1000, {"status": "REFUND", "amount": 1}),
])
def test_get_vat_return_status(output_vat, input_vat, expected):
    assert tax.get_vat_return_status(output_vat, input_vat) == expected


def test_sum_monthly_tax():
    records = [
        {"date": "2024-03-31", "vat": 300},
        {"date": "2024-02-01", "vat": 100},
        {"date": "2024-03-01", "vat": 200, "status": "PAID"},
        {"date": "2024-03-15", "vat": 999, "status": "CANCELED"},
        {"date": "2024-04-01 00:00:00", "vat": 50},
    ]
    result = tax.sum_monthly_tax(records)
    assert result == {"2024-02": 100, "2024-03": 500, "2024-04": 50}
    assert list(result.keys()) == ["2024-02", "2024-03", "2024-04"]
    assert tax.sum_monthly_tax([]) == {}
    assert tax.sum_monthly_tax([{"date": "2024-05-01", "vat": 10, "status": "CANCELED"}]) == {}


def test_ensure_tax_schema_idempotent(conn):
    tax.ensure_tax_schema(conn)
    tax.ensure_tax_schema(conn)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE name = 'receipts'").fetchall()
    assert len(rows) == 1


def test_record_receipt(conn):
    tax.ensure_tax_schema(conn)
    first = tax.record_receipt(conn, 10, "CASH_RECEIPT", 11000, "2024-03-05 10:00:00")
    second = tax.record_receipt(conn, 11, "TAX_INVOICE", 10999, "2024-03-06 10:00:00")
    third = tax.record_receipt(conn, 12, "CARD_SLIP", 22, "2024-03-07 10:00:00")
    assert second == first + 1 and third == second + 1
    row = dict(conn.execute("SELECT * FROM receipts WHERE id = ?", (second,)).fetchone())
    assert row == {"id": second, "order_id": 11, "receipt_type": "TAX_INVOICE",
                   "supply": 10000, "vat": 999, "issued_at": "2024-03-06 10:00:00"}


@pytest.mark.parametrize("receipt_type", ["NONE", "INVOICE", ""])
def test_record_receipt_invalid_type(conn, receipt_type):
    tax.ensure_tax_schema(conn)
    with pytest.raises(ValueError):
        tax.record_receipt(conn, 1, receipt_type, 1000, "2024-03-05 10:00:00")
    assert conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0


def test_sum_receipts_by_month(conn):
    tax.ensure_tax_schema(conn)
    tax.record_receipt(conn, 1, "CASH_RECEIPT", 11000, "2024-03-01 00:00:00")
    tax.record_receipt(conn, 2, "TAX_INVOICE", 22000, "2024-03-31 23:59:59")
    tax.record_receipt(conn, 3, "CARD_SLIP", 5500, "2024-04-01 00:00:00")
    assert tax.sum_receipts_by_month(conn, "2024-03") == {"count": 2, "supply": 30000, "vat": 3000}
    assert tax.sum_receipts_by_month(conn, "2024-04") == {"count": 1, "supply": 5000, "vat": 500}
    assert tax.sum_receipts_by_month(conn, "2024-05") == {"count": 0, "supply": 0, "vat": 0}
