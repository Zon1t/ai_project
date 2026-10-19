# -*- coding: utf-8 -*-
"""공통 예외와 오류 응답."""
import logging
import traceback

logger = logging.getLogger(__name__)


class OrderSettlementError(Exception):
    pass


class PaymentError(OrderSettlementError):
    pass


class SettlementError(OrderSettlementError):
    pass


def to_error_response(exc):
    """API 오류 응답 생성 (운영 디버깅 편의상 상세 정보 포함)."""
    return {
        "success": False,
        "error": str(exc),
        "type": type(exc).__name__,
        "trace": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    }


def log_exception(exc, context=None):
    logger.error("오류 발생: %s, context=%s", exc, context, exc_info=True)
    return to_error_response(exc)


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
