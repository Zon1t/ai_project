"""예측 신호 임계값 정하기 (PRD N5①, F5-0).

평가(test) 분할 티켓마다 학습된 혼합형 모델로 1위 점수와 최대 유사도를 계산하고,
config.PREDICTION_SIGNAL_PERCENTILE 백분위를 임계값으로 data/models/에 저장한다.
학습 분할·홀드아웃은 쓰지 않는다. 같은 모델·데이터면 같은 결과가 나온다 (난수 없음).

실행: python -m sr_agent.ml.calibrate  (PYTHONPATH=src)
"""

import json
from pathlib import Path

import numpy as np

from sr_agent import config
from sr_agent.ml.corpus import load_tickets, ticket_text
from sr_agent.tools.predict import FaultLocator, get_locator

SIGNAL_KEYS = ("top1", "emb_max", "tfidf_max")


def signal_table(locator: FaultLocator, tickets: list[dict]) -> list[dict]:
    """티켓마다 신호 값과 모델 Top-3 적중 여부."""
    methods = [c.method for c in locator.candidates]
    rows = []
    for t in tickets:
        X = locator.features_for(ticket_text(t))
        scores = locator.ranker.predict(X)
        top3 = [methods[j] for j in np.argsort(-scores, kind="stable")[:3]]
        rows.append({**locator.raw_signal(X, scores), "top3_hit": t["fixed_method"] in top3})
    return rows


def thresholds_from(rows: list[dict], percentile: float) -> dict:
    return {k: round(float(np.percentile([r[k] for r in rows], percentile)), 4) for k in SIGNAL_KEYS}


def run(path: Path | None = None, percentile: float = config.PREDICTION_SIGNAL_PERCENTILE) -> dict:
    """평가 분할로 임계값을 계산해 저장하고 요약을 돌려준다."""
    tickets = [t for t in load_tickets() if t["split"] == "test"]
    rows = signal_table(get_locator(), tickets)
    thresholds = thresholds_from(rows, percentile)
    flagged = [r for r in rows if any(r[k] < v for k, v in thresholds.items())]
    result = {
        "split": "test",
        "n": len(rows),
        "percentile": percentile,
        "thresholds": thresholds,
        "percentiles": {k: {p: round(float(np.percentile([r[k] for r in rows], p)), 4) for p in (1, 5, 10, 50)}
                        for k in SIGNAL_KEYS},
        "flagged": len(flagged),  # 평가 분할 정상 티켓 중 "낮음"으로 걸리는 수
        "flagged_top3_hit": round(float(np.mean([r["top3_hit"] for r in flagged])), 3) if flagged else None,
    }
    path = Path(path or config.PREDICTION_SIGNAL_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return result


def main() -> None:
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
