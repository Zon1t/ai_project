"""순위 지표 Top-k, MRR과 나눠 보기 (PRD F3-3)."""

import numpy as np


def ranks(scores: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """티켓별 정답 순위 (1부터). 동점은 불리하게 센다 (정답과 같은 점수인 후보는 모두 앞에 있다고 봄)."""
    true_scores = (scores * labels).sum(axis=1, keepdims=True)
    return (scores >= true_scores).sum(axis=1)


def summarize(r: np.ndarray) -> dict:
    """{"n", "top1", "top3", "mrr"}."""
    if len(r) == 0:
        return {"n": 0, "top1": None, "top3": None, "mrr": None}
    return {
        "n": int(len(r)),
        "top1": round(float((r <= 1).mean()), 4),
        "top3": round(float((r <= 3).mean()), 4),
        "mrr": round(float((1.0 / r).mean()), 4),
    }


def breakdown(r: np.ndarray, tickets: list[dict]) -> list[dict]:
    """전체 · 스택트레이스 유무 · 주입 방식(연산자)별 지표."""
    groups = [("전체", "전체", np.ones(len(tickets), dtype=bool))]
    for has, label in ((True, "있음"), (False, "없음")):
        groups.append(("스택트레이스", label, np.array([t["has_stacktrace"] == has for t in tickets])))
    for inject in sorted({t["inject_type"] for t in tickets}):
        groups.append(("주입 방식", inject, np.array([t["inject_type"] == inject for t in tickets])))
    for op in sorted({t["operator"] for t in tickets}):
        groups.append(("연산자", op, np.array([t["operator"] == op for t in tickets])))
    return [{"group": g, "value": v, **summarize(r[mask])} for g, v, mask in groups]
