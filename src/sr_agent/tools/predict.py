"""결함 위치 예측 도구 predict_fault_location (PRD F3-2, 7.3).

학습된 혼합형 모델(data/models/fault_ranker.json)로 대상 프로젝트 함수의 순위를 매기고,
상위 k개마다 SHAP 기여도가 큰 피처 3개를 근거로 붙인다.
후보 함수는 target/ 원본 기준이다 (학습 때와 같은 목록이어야 하며, 다르면 재학습을 요구한다).
모델·임베딩은 처음 호출할 때 한 번만 올린다. 실패는 예외 대신 {"error": ...}로 돌려준다.
"""

import json
import threading
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from xgboost import XGBRanker

from sr_agent import config
from sr_agent.ml.corpus import Candidate, load_candidates
from sr_agent.ml.features import FEATURES, Embedder, build_features

FEATURE_LABELS = {
    "tfidf_sim": "티켓-코드 단어 겹침 (TF-IDF)",
    "emb_sim": "티켓-코드 의미 유사도 (임베딩)",
    "trace_in": "스택트레이스에 이 함수가 나옴",
    "trace_last": "스택트레이스의 예외 발생 위치",
    "trace_module": "스택트레이스에 같은 모듈이 나옴",
    "loc": "함수 길이 (줄)",
    "complexity": "함수 분기 복잡도",
    "past_defects": "과거 결함 수",
}


class FaultLocator:
    """혼합형 모델 + TF-IDF + 임베딩 + SHAP. 한 번 만들어 여러 티켓에 쓴다."""

    def __init__(
        self,
        model_path: Path | None = None,
        meta_path: Path | None = None,
        candidates: list[Candidate] | None = None,
        embedder: Embedder | None = None,
    ):
        import shap

        meta = json.loads(Path(meta_path or config.FAULT_MODEL_META_PATH).read_text(encoding="utf-8"))
        self.features: list[str] = meta["features"]
        self.candidates = candidates if candidates is not None else load_candidates()
        if [c.method for c in self.candidates] != meta["candidates"]:
            raise ValueError("대상 프로젝트 함수 목록이 학습 때와 다름 (python -m sr_agent.ml.train으로 재학습 필요)")
        self.ranker = XGBRanker()
        self.ranker.load_model(Path(model_path or config.FAULT_MODEL_PATH))
        docs = [c.document for c in self.candidates]
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=config.TFIDF_NGRAM_RANGE,
                                          sublinear_tf=True)
        self.cand_tfidf = self.vectorizer.fit_transform(docs)
        self.embedder = embedder or Embedder(meta["embedding_model"])
        self.cand_emb = self.embedder.encode(docs)
        self.explainer = shap.TreeExplainer(self.ranker)
        self._cols = [FEATURES.index(f) for f in self.features]

    def features_for(self, ticket_text: str) -> np.ndarray:
        """[후보, 모델 피처]. 티켓 본문에 traceback이 있으면 스택트레이스 피처에 반영된다."""
        tfidf = (self.vectorizer.transform([ticket_text]) @ self.cand_tfidf.T).toarray()
        emb = self.embedder.encode([ticket_text], save=False) @ self.cand_emb.T
        X, _ = build_features([{"stacktrace": ticket_text}], self.candidates, tfidf, emb, counts={}, counted=set())
        return X[0][:, self._cols]

    def predict(self, ticket_text: str, top_k: int = 3) -> list[dict]:
        X = self.features_for(ticket_text)
        scores = self.ranker.predict(X)
        top = np.argsort(-scores, kind="stable")[:top_k]
        contributions = np.asarray(self.explainer.shap_values(X[top]))
        results = []
        for rank, j in enumerate(top):
            order = np.argsort(-contributions[rank], kind="stable")[: config.PREDICT_REASONS]
            results.append({
                "method": self.candidates[j].method,
                "score": round(float(scores[j]), 4),
                "reasons": [{
                    "feature": self.features[f],
                    "label": FEATURE_LABELS[self.features[f]],
                    "value": round(float(X[j, f]), 4),
                    "contribution": round(float(contributions[rank, f]), 4),
                } for f in order],
            })
        return results


_locator: FaultLocator | None = None
_lock = threading.Lock()


def get_locator() -> FaultLocator:
    """프로세스당 하나만 만든다 (모델·임베딩 로딩이 무겁다)."""
    global _locator
    with _lock:
        if _locator is None:
            _locator = FaultLocator()
        return _locator


def predict_fault_location(ticket_text: str, top_k: int = 3) -> list[dict] | dict:
    """티켓 문장으로 결함 위치 Top-k를 예측한다.

    반환: [{"method": "모듈.함수", "score": float, "reasons": [{feature, label, value, contribution}] × 3}]
    """
    if not isinstance(ticket_text, str) or not ticket_text.strip():
        return {"error": "ticket_text는 비어 있지 않은 문자열이어야 함"}
    if len(ticket_text) > config.MAX_TICKET_CHARS:
        return {"error": f"ticket_text는 최대 {config.MAX_TICKET_CHARS}자"}
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= config.PREDICT_MAX_TOP_K:
        return {"error": f"top_k는 1~{config.PREDICT_MAX_TOP_K} 사이 정수"}
    try:
        return get_locator().predict(ticket_text, top_k)
    except Exception as e:  # noqa: BLE001 - 도구는 예외 대신 오류 값을 돌려준다
        return {"error": f"{type(e).__name__}: {e}"}
