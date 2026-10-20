"""(티켓, 후보 함수) 쌍의 피처 (PRD 6장).

past_defects는 학습 분할 결함으로만 센다. 학습 티켓은 자기 결함을 빼고 센다 (정답이 피처에 새지 않게).
"""

import hashlib
import re
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from sr_agent import config
from sr_agent.ml.corpus import Candidate

FEATURES = ["tfidf_sim", "emb_sim", "trace_in", "trace_last", "trace_module", "loc", "complexity", "past_defects"]
_FRAME = re.compile(r'File "(?:[\w/]+/)?(\w+)\.py", line \d+, in (\w+)')


def tfidf_scores(ticket_texts: list[str], candidates: list[Candidate]) -> np.ndarray:
    """[티켓 수, 후보 수] TF-IDF 코사인 유사도. IDF는 후보 함수 코드로만 학습한다 (티켓 정보 미사용)."""
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=config.TFIDF_NGRAM_RANGE, sublinear_tf=True)
    cand = vectorizer.fit_transform([c.document for c in candidates])
    tick = vectorizer.transform(ticket_texts)
    return (tick @ cand.T).toarray()  # 행이 L2 정규화되어 있어 내적 = 코사인


class Embedder:
    """sentence-transformers 임베딩. 텍스트별로 캐시해 다시 계산하지 않는다."""

    def __init__(self, key: str, cache_dir: Path | None = None):
        self.key = key
        self.model_name = config.EMBED_MODELS[key]
        self.cache_path = Path(cache_dir or config.CACHE_DIR) / f"emb_{key}.npz"
        self._model = None

    def _load_cache(self) -> dict[str, np.ndarray]:
        if not self.cache_path.is_file():
            return {}
        data = np.load(self.cache_path)
        return dict(zip(data["keys"].tolist(), data["vectors"], strict=True))

    def _model_instance(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name, device="cpu")
            self._model.max_seq_length = config.EMBED_MAX_SEQ_LEN
        return self._model

    def encode(self, texts: list[str]) -> np.ndarray:
        """L2 정규화된 임베딩 [len(texts), dim]."""
        keys = [hashlib.sha1(f"{self.model_name}\n{t}".encode()).hexdigest() for t in texts]
        cache = self._load_cache()
        missing = sorted({k: t for k, t in zip(keys, texts, strict=True) if k not in cache}.items())
        if missing:
            vectors = self._model_instance().encode(
                [t for _, t in missing], batch_size=config.EMBED_BATCH_SIZE, normalize_embeddings=True,
                show_progress_bar=False,
            )
            cache.update({k: v for (k, _), v in zip(missing, vectors, strict=True)})
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(self.cache_path, keys=np.array(list(cache)), vectors=np.stack(list(cache.values())))
        return np.stack([cache[k] for k in keys])


def embedding_scores(embedder: Embedder, ticket_texts: list[str], candidates: list[Candidate]) -> np.ndarray:
    """[티켓 수, 후보 수] 임베딩 코사인 유사도."""
    return embedder.encode(ticket_texts) @ embedder.encode([c.document for c in candidates]).T


def trace_frames(stacktrace: str) -> list[tuple[str, str]]:
    """traceback의 (모듈, 함수) 목록. 바깥 프레임부터."""
    return _FRAME.findall(stacktrace or "")


def defect_counts(train_tickets: list[dict]) -> Counter:
    """학습 분할 결함의 함수별 개수 (결함 단위, 티켓 2건이어도 1개)."""
    mutants = {t["mutant_id"]: t["fixed_method"] for t in train_tickets if t["split"] == "train"}
    return Counter(mutants.values())


def build_features(
    tickets: list[dict],
    candidates: list[Candidate],
    tfidf: np.ndarray,
    emb: np.ndarray,
    counts: Counter,
) -> tuple[np.ndarray, np.ndarray]:
    """피처 [티켓, 후보, FEATURES]와 정답 라벨 [티켓, 후보]."""
    n_t, n_c = len(tickets), len(candidates)
    X = np.zeros((n_t, n_c, len(FEATURES)), dtype=np.float32)
    y = np.zeros((n_t, n_c), dtype=np.int32)
    X[:, :, FEATURES.index("tfidf_sim")] = tfidf
    X[:, :, FEATURES.index("emb_sim")] = emb
    X[:, :, FEATURES.index("loc")] = [c.loc for c in candidates]
    X[:, :, FEATURES.index("complexity")] = [c.complexity for c in candidates]
    past = np.array([counts.get(c.method, 0) for c in candidates], dtype=np.float32)
    methods = [c.method for c in candidates]
    for i, t in enumerate(tickets):
        y[i, methods.index(t["fixed_method"])] = 1
        X[i, :, FEATURES.index("past_defects")] = past
        if t["split"] == "train":  # 자기 결함은 빼고 센다
            X[i, methods.index(t["fixed_method"]), FEATURES.index("past_defects")] -= 1
        frames = trace_frames(t.get("stacktrace", ""))
        if frames:
            in_trace = {f"{m}.{f}" for m, f in frames}
            modules = {m for m, _ in frames}
            last = "{}.{}".format(*frames[-1])
            for j, c in enumerate(candidates):
                X[i, j, FEATURES.index("trace_in")] = c.method in in_trace
                X[i, j, FEATURES.index("trace_last")] = c.method == last
                X[i, j, FEATURES.index("trace_module")] = c.module in modules
    return X, y
