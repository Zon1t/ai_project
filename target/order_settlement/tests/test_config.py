# -*- coding: utf-8 -*-
from datetime import date

from order_settlement import config


def test_get_setting():
    assert config.get_setting("COUPON_MIN_ORDER") == 100000
    assert config.get_setting("NO_SUCH_KEY", 1) == 1


def test_override():
    config.set_override("SHIPPING_FEE", 5000)
    try:
        assert config.get_setting("SHIPPING_FEE") == 5000
    finally:
        config.set_override("SHIPPING_FEE", None)
    assert config.get_setting("SHIPPING_FEE") == 3000


def test_timezone():
    assert config.get_timezone().zone == "Asia/Seoul"


def test_commission_rate():
    assert config.get_commission_rate("PARTNER") == 10
    assert config.get_commission_rate("PREMIUM") == 8
    assert config.get_commission_rate("UNKNOWN") == 12


def test_holidays():
    holidays = config.get_holidays()
    assert date(2024, 1, 1) in holidays
    assert len(holidays) == len(config.HOLIDAYS)
