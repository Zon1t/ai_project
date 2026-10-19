# -*- coding: utf-8 -*-
from datetime import date, datetime, timedelta

import pytest
import pytz

from order_settlement import date_utils


def test_to_kst_naive():
    kst = date_utils.to_kst(datetime(2024, 1, 31, 15, 0))
    assert (kst.year, kst.month, kst.day, kst.hour) == (2024, 2, 1, 0)
    assert kst.utcoffset() == timedelta(hours=9)


def test_to_kst_aware():
    aware = pytz.utc.localize(datetime(2024, 6, 1, 0, 0))
    assert date_utils.to_kst(aware).hour == 9


def test_now_functions():
    assert date_utils.now_utc().tzinfo is None
    assert date_utils.now_kst().utcoffset() == timedelta(hours=9)


def test_parse_format_roundtrip():
    d = date_utils.parse_date("2024-02-29")
    assert d == date(2024, 2, 29)
    assert date_utils.format_date(d) == "2024-02-29"


def test_weekend_and_holiday():
    assert date_utils.is_weekend(date(2024, 3, 2)) is True
    assert date_utils.is_weekend(date(2024, 3, 4)) is False
    assert date_utils.is_holiday(date(2024, 3, 1)) is True
    assert date_utils.is_business_day(date(2024, 3, 1)) is False
    assert date_utils.is_business_day(date(2024, 3, 4)) is True


def test_add_business_days():
    # 3/1 공휴일, 3/2~3 주말
    assert date_utils.add_business_days(date(2024, 2, 29), 1) == date(2024, 3, 4)
    assert date_utils.add_business_days(date(2024, 2, 29), 5) == date(2024, 3, 8)
    assert date_utils.add_business_days(date(2024, 3, 4), 0) == date(2024, 3, 4)


@pytest.mark.parametrize("year,leap", [(2024, True), (2023, False), (1900, False), (2000, True)])
def test_is_leap_year(year, leap):
    assert date_utils.is_leap_year(year) is leap


def test_last_day_of_month():
    assert date_utils.last_day_of_month(2024, 2) == 29
    assert date_utils.last_day_of_month(2023, 2) == 28
    assert date_utils.last_day_of_month(2024, 4) == 30
    assert date_utils.last_day_of_month(2024, 12) == 31


def test_days_between():
    assert date_utils.days_between(date(2024, 2, 28), date(2024, 3, 1)) == 2
