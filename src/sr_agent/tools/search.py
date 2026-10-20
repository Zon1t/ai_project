"""검색 도구 search_history, search_rules (PRD F4-1, F4-2, 7.3).

색인은 python -m sr_agent.rag.index로 미리 만든다. 실패는 예외 대신 {"error": ...}로 돌려준다.
"""

import threading

from sr_agent import config
from sr_agent.rag.index import KnowledgeBase

_kb: KnowledgeBase | None = None
_lock = threading.Lock()


def get_kb() -> KnowledgeBase:
    """프로세스당 하나만 연다."""
    global _kb
    with _lock:
        if _kb is None:
            _kb = KnowledgeBase()
        return _kb


def _check(text, name: str, limit: int, top_k) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return f"{name}는 비어 있지 않은 문자열이어야 함"
    if len(text) > limit:
        return f"{name}는 최대 {limit}자"
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= config.SEARCH_MAX_TOP_K:
        return f"top_k는 1~{config.SEARCH_MAX_TOP_K} 사이 정수"
    return None


def search_history(query: str, top_k: int = 3) -> list[dict] | dict:
    """증상 문장과 비슷한 과거 장애·조치 이력 (학습 분할만).

    반환: [{"history_id", "ticket", "method", "root_cause", "fix_summary", "score"}]
    """
    if problem := _check(query, "query", config.MAX_TICKET_CHARS, top_k):
        return {"error": problem}
    try:
        return get_kb().search_history(query, top_k)
    except Exception as e:  # noqa: BLE001 - 도구는 예외 대신 오류 값을 돌려준다
        return {"error": f"{type(e).__name__}: {e}"}


def search_rules(code: str, top_k: int = 5) -> list[dict] | dict:
    """코드와 관련된 사내 규칙.

    반환: [{"rule_id", "title", "description", "good_example", "severity", "matched_pattern", "score"}]
    """
    if problem := _check(code, "code", config.MAX_CODE_CHARS, top_k):
        return {"error": problem}
    try:
        return get_kb().search_rules(code, top_k)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
