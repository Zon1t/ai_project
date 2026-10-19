# -*- coding: utf-8 -*-
from order_settlement import errors


def _raise():
    raise errors.PaymentError("결제 실패")


def test_to_error_response():
    try:
        _raise()
    except errors.PaymentError as e:
        resp = errors.to_error_response(e)
    assert resp["success"] is False
    assert resp["error"] == "결제 실패"
    assert resp["type"] == "PaymentError"
    assert "Traceback" in resp["trace"]


def test_log_exception():
    resp = errors.log_exception(ValueError("bad"), {"order_id": 1})
    assert resp["type"] == "ValueError"


def test_exception_hierarchy():
    assert issubclass(errors.SettlementError, errors.OrderSettlementError)


def test_safe_int():
    assert errors.safe_int("12") == 12
    assert errors.safe_int("x") == 0
    assert errors.safe_int(None, -1) == -1
