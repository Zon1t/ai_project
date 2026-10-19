# -*- coding: utf-8 -*-
"""날짜·영업일 유틸. 내부 시각은 UTC naive, 표시는 KST."""
import calendar
from datetime import datetime, timedelta

import pytz

from order_settlement import config


def now_utc():
    return datetime.utcnow()


def now_kst():
    return pytz.utc.localize(datetime.utcnow()).astimezone(config.get_timezone())


def to_kst(dt_utc):
    if dt_utc.tzinfo is None:
        dt_utc = pytz.utc.localize(dt_utc)
    return dt_utc.astimezone(config.get_timezone())


def parse_date(text):
    return datetime.strptime(text, "%Y-%m-%d").date()


def format_date(d):
    return d.strftime("%Y-%m-%d")


def is_weekend(d):
    return d.weekday() >= 5


def is_holiday(d):
    return d in config.get_holidays()


def is_business_day(d):
    return not is_weekend(d) and not is_holiday(d)


def add_business_days(d, n):
    """d 다음 날부터 세어 n번째 영업일."""
    current = d
    added = 0
    while added < n:
        current += timedelta(days=1)
        if is_business_day(current):
            added += 1
    return current


def is_leap_year(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def last_day_of_month(year, month):
    return calendar.monthrange(year, month)[1]


def days_between(start, end):
    return (end - start).days
