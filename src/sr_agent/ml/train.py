"""결함 위치 모델 3종 학습·비교 (PRD F3-1, F3-3).

① TF-IDF 단독 ② 임베딩 유사도 단독 ③ XGBoost Ranker 혼합형.
- 하이퍼파라미터: 학습(train) 분할 안에서 결함 단위 교차검증으로 고른다.
- 주 결과: train으로 학습, test 분할로 한 번 평가한다. holdout(E2E용)은 쓰지 않는다.
- 참고 결과: train+test 전체로 결함 단위·함수 단위 교차검증 (함수 단위는 평가 함수를 학습에서 통째로 뺌).
- past_defects는 fold마다 그 fold의 학습 부분 결함으로만 센다.
같은 데이터·시드면 같은 결과가 나온다.

실행: python -m sr_agent.ml.train  (PYTHONPATH=src)
"""

import argparse
import csv
import hashlib
import itertools
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
from sklearn.model_selection import GroupKFold
from xgboost import XGBRanker

from sr_agent import config
from sr_agent.ml.corpus import Candidate, load_candidates, load_tickets, ticket_text
from sr_agent.ml.features import (
    FEATURES,
    Embedder,
    build_features,
    defect_counts,
    embedding_scores,
    set_past_defects,
    tfidf_scores,
)
from sr_agent.ml.metrics import breakdown, ranks, summarize

MODEL_TFIDF = "① TF-IDF"
MODEL_EMB = "② 임베딩"
MODEL_RANKER = "③ 혼합형 (XGBoost Ranker)"
MODEL_RANKER_PAST = "③-A 혼합형 + 과거 결함 수 (비교용)"
RANKER_FEATURES = [f for f in FEATURES if f not in config.RANKER_EXCLUDED_FEATURES]


def _flat(X: np.ndarray) -> np.ndarray:
    return X.reshape(-1, X.shape[-1])


def _select(X: np.ndarray, features: list[str]) -> np.ndarray:
    return X[:, :, [FEATURES.index(f) for f in features]]


def train_ranker(X: np.ndarray, y: np.ndarray, params: dict, seed: int = config.SEED,
                 features: list[str] = RANKER_FEATURES) -> XGBRanker:
    """X [티켓, 후보, FEATURES], y [티켓, 후보]. 티켓 하나가 쿼리 하나. features 열만 쓴다."""
    qid = np.repeat(np.arange(X.shape[0]), X.shape[1])
    ranker = XGBRanker(**config.RANKER_PARAMS, **params, random_state=seed)
    ranker.fit(_flat(_select(X, features)), y.reshape(-1), qid=qid)
    return ranker


def predict_scores(ranker: XGBRanker, X: np.ndarray, features: list[str] = RANKER_FEATURES) -> np.ndarray:
    return ranker.predict(_flat(_select(X, features))).reshape(X.shape[0], X.shape[1])


def param_grid(grid: dict[str, list] | None = None) -> list[dict]:
    grid = grid or config.RANKER_GRID
    keys = sorted(grid)
    return [dict(zip(keys, values, strict=True)) for values in itertools.product(*(grid[k] for k in keys))]


class Dataset:
    """train+test 티켓의 피처. past_defects만 fold마다 다시 채운다."""

    def __init__(self, tickets: list[dict], candidates: list[Candidate], tfidf: np.ndarray, emb: np.ndarray):
        self.tickets = tickets
        self.candidates = candidates
        self.tfidf = tfidf
        self.emb = emb
        self.X, self.y = build_features(tickets, candidates, tfidf, emb, counts={}, counted=set())

    def fold(self, fit_idx: np.ndarray, eval_idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """fit_idx 티켓의 결함으로 past_defects를 센 피처 (학습용, 평가용)."""
        fit_tickets = [self.tickets[i] for i in fit_idx]
        counts = defect_counts(fit_tickets, split=None)
        counted = {t["mutant_id"] for t in fit_tickets}
        X_fit, X_eval = self.X[fit_idx].copy(), self.X[eval_idx].copy()
        set_past_defects(X_fit, fit_tickets, self.candidates, counts, counted)
        set_past_defects(X_eval, [self.tickets[i] for i in eval_idx], self.candidates, counts, counted)
        return X_fit, X_eval

    def ranker_ranks(self, fit_idx: np.ndarray, eval_idx: np.ndarray, params: dict, seed: int,
                     features: list[str] = RANKER_FEATURES) -> np.ndarray:
        X_fit, X_eval = self.fold(fit_idx, eval_idx)
        ranker = train_ranker(X_fit, self.y[fit_idx], params, seed, features)
        return ranks(predict_scores(ranker, X_eval, features), self.y[eval_idx])

    def groups(self, key: str, idx: np.ndarray) -> np.ndarray:
        return np.array([self.tickets[i][key] for i in idx])


def _cv_splits(ds: Dataset, idx: np.ndarray, group_key: str, seed: int) -> list[tuple[np.ndarray, np.ndarray]]:
    groups = ds.groups(group_key, idx)
    n_splits = min(config.CV_FOLDS, len(set(groups)))
    kfold = GroupKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    return [(idx[a], idx[b]) for a, b in kfold.split(idx, groups=groups)]


def tune(ds: Dataset, train_idx: np.ndarray, seed: int, grid: dict | None = None) -> tuple[dict, list[dict]]:
    """학습 분할 안 결함 단위 교차검증으로 그리드를 평가해 MRR이 가장 높은 값을 고른다."""
    splits = _cv_splits(ds, train_idx, "mutant_id", seed)
    table = []
    for params in param_grid(grid):
        r = np.concatenate([ds.ranker_ranks(a, b, params, seed) for a, b in splits])
        table.append({"params": params, **summarize(r)})
    best = max(table, key=lambda row: (row["mrr"], row["top3"]))
    return best["params"], table


def cross_validate(ds: Dataset, idx: np.ndarray, group_key: str, params: dict, seed: int) -> dict:
    """교차검증 순위를 모아 세 모델의 나눠 보기 지표를 낸다."""
    order, r_rank, r_past = [], [], []
    for fit, ev in _cv_splits(ds, idx, group_key, seed):
        order.append(ev)
        r_rank.append(ds.ranker_ranks(fit, ev, params, seed))
        r_past.append(ds.ranker_ranks(fit, ev, params, seed, FEATURES))
    order = np.concatenate(order)
    tickets = [ds.tickets[i] for i in order]
    return {
        MODEL_TFIDF: breakdown(ranks(ds.tfidf[order], ds.y[order]), tickets),
        MODEL_EMB: breakdown(ranks(ds.emb[order], ds.y[order]), tickets),
        MODEL_RANKER: breakdown(np.concatenate(r_rank), tickets),
        MODEL_RANKER_PAST: breakdown(np.concatenate(r_past), tickets),
    }


def evaluate(
    tickets: list[dict],
    candidates: list[Candidate],
    embedder: Embedder,
    seed: int = config.SEED,
    grid: dict | None = None,
    progress: Callable[[str], None] = lambda msg: None,
) -> dict:
    """튜닝 → train 학습·test 평가 → 교차검증. 반환: 지표, 튜닝표, 피처 중요도, 학습된 ranker."""
    methods = {c.method for c in candidates}
    if unknown := sorted({t["fixed_method"] for t in tickets} - methods):
        raise ValueError(f"후보에 없는 정답 함수: {unknown}")
    pool = [t for t in tickets if t["split"] in ("train", "test")]
    texts = [ticket_text(t) for t in pool]
    progress(f"TF-IDF·임베딩 ({len(pool)}건)")
    ds = Dataset(pool, candidates, tfidf_scores(texts, candidates), embedding_scores(embedder, texts, candidates))
    train_idx = np.array([i for i, t in enumerate(pool) if t["split"] == "train"])
    test_idx = np.array([i for i, t in enumerate(pool) if t["split"] == "test"])
    test = [pool[i] for i in test_idx]

    progress("하이퍼파라미터 튜닝")
    params, tuning = tune(ds, train_idx, seed, grid)
    X_fit, X_test = ds.fold(train_idx, test_idx)
    ranker = train_ranker(X_fit, ds.y[train_idx], params, seed)
    past_ranker = train_ranker(X_fit, ds.y[train_idx], params, seed, FEATURES)
    results = {
        MODEL_TFIDF: breakdown(ranks(ds.tfidf[test_idx], ds.y[test_idx]), test),
        f"{MODEL_EMB} ({embedder.key})": breakdown(ranks(ds.emb[test_idx], ds.y[test_idx]), test),
        MODEL_RANKER: breakdown(ranks(predict_scores(ranker, X_test), ds.y[test_idx]), test),
        MODEL_RANKER_PAST: breakdown(ranks(predict_scores(past_ranker, X_test, FEATURES), ds.y[test_idx]), test),
    }
    train_fit = summarize(ranks(predict_scores(ranker, X_fit), ds.y[train_idx]))

    all_idx = np.arange(len(pool))
    progress("교차검증 (결함 단위)")
    cv_mutant = cross_validate(ds, all_idx, "mutant_id", params, seed)
    progress("교차검증 (함수 단위)")
    cv_method = cross_validate(ds, all_idx, "fixed_method", params, seed)
    train_tickets = [pool[i] for i in train_idx]
    return {
        "counts": {"candidates": len(candidates), "train_tickets": len(train_idx), "test_tickets": len(test_idx),
                   "train_mutants": len({t["mutant_id"] for t in train_tickets}),
                   "test_mutants": len({t["mutant_id"] for t in test})},
        "embedding_model": embedder.key,
        "params": params,
        "tuning": tuning,
        "results": results,
        "cv": {"결함 단위": cv_mutant, "함수 단위": cv_method},
        "train_results": {MODEL_RANKER: train_fit},
        "feature_importance": dict(zip(RANKER_FEATURES, (round(float(v), 4) for v in ranker.feature_importances_),
                                       strict=True)),
        "defect_counts": dict(sorted(defect_counts(train_tickets).items())),
        "ranker": ranker,
    }


def _fmt(v) -> str:
    return "-" if v is None else f"{v:.3f}"


def _cells(row: dict) -> str:
    return f"{_fmt(row['top1'])} / {_fmt(row['top3'])} / {_fmt(row['mrr'])}"


def _breakdown_table(results: dict[str, list[dict]]) -> list[str]:
    names = list(results)
    lines = ["| 구분 | 값 | n | " + " | ".join(f"{n}<br>Top-1 / Top-3 / MRR" for n in names) + " |",
             "|---|---|---|" + "---|" * len(names)]
    for i, row in enumerate(results[names[0]]):
        lines.append(f"| {row['group']} | {row['value']} | {row['n']} | "
                     + " | ".join(_cells(results[n][i]) for n in names) + " |")
    return lines


def render_report(result: dict, kpi_ok: bool) -> str:
    """eval/results/fault_localization.md 본문."""
    c = result["counts"]
    overall = {n: rows[0] for n, rows in result["results"].items()}
    lines = [
        "# 결함 위치 예측: 모델 3종 비교 (F3-1, F3-3)",
        "",
        f"- 데이터: 평가(test) 티켓 {c['test_tickets']}건 / 결함 {c['test_mutants']}개, "
        f"학습(train) 티켓 {c['train_tickets']}건 / 결함 {c['train_mutants']}개, 후보 함수 {c['candidates']}개",
        f"- 임베딩 모델: `{result['embedding_model']}`, 시드 {config.SEED}",
        f"- 혼합형 하이퍼파라미터 (학습 분할 결함 단위 {config.CV_FOLDS}-fold로 선택): `{result['params']}`",
        f"- 혼합형 피처: {', '.join(RANKER_FEATURES)}. 과거 결함 수(past_defects)는 처음 보는 함수에서 성능이 "
        "무너져 주 모델에서 빼고, ③-A로 비교만 한다",
        "- 동점은 불리하게 센다 (정답과 같은 점수인 후보가 모두 앞에 있다고 봄)",
        "",
        "## 비교표 (평가 분할)",
        "",
        "| 모델 | Top-1 | Top-3 | MRR |",
        "|---|---|---|---|",
    ]
    lines += [f"| {n} | {_fmt(o['top1'])} | {_fmt(o['top3'])} | {_fmt(o['mrr'])} |" for n, o in overall.items()]
    top3 = overall[MODEL_RANKER]["top3"]
    lines += ["", f"**KPI (혼합형 Top-3 ≥ {config.KPI_TOP3:.2f})**: {top3:.3f} → {'달성' if kpi_ok else '미달'}", "",
              "## 나눠 보기 (평가 분할)", ""]
    lines += _breakdown_table(result["results"])
    for kind, cv in result["cv"].items():
        note = ("평가 함수를 학습에서 통째로 뺌 — 처음 보는 함수에 대한 일반화" if kind == "함수 단위"
                else "표본 흔들림 확인용")
        lines += ["", f"## 교차검증: {kind} {config.CV_FOLDS}-fold (train+test, {note})", ""]
        lines += _breakdown_table(cv)
    lines += ["", "## 하이퍼파라미터 튜닝 (학습 분할 결함 단위 교차검증)", "", "| 파라미터 | Top-1 | Top-3 | MRR |",
              "|---|---|---|---|"]
    lines += [f"| `{row['params']}` | {_fmt(row['top1'])} | {_fmt(row['top3'])} | {_fmt(row['mrr'])} |"
              for row in result["tuning"]]
    lines += ["", "## 혼합형 피처 중요도 (gain)", "", "| 피처 | 중요도 |", "|---|---|"]
    lines += [f"| {f} | {v:.3f} |" for f, v in sorted(result["feature_importance"].items(), key=lambda x: -x[1])]
    tr = result["train_results"][MODEL_RANKER]
    lines += ["", f"참고: 혼합형의 학습 분할 성능 {_cells(tr)} (평가 분할과의 차이로 과적합 정도를 본다)", ""]
    return "\n".join(lines)


def _file_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(
    tickets_csv: Path | None = None,
    embed_key: str = config.EMBED_MODEL,
    results_dir: Path | None = None,
    model_path: Path | None = None,
    meta_path: Path | None = None,
    seed: int = config.SEED,
) -> dict:
    """학습·평가 후 eval/results/와 data/models/에 저장하고 요약을 돌려준다."""
    tickets_csv = Path(tickets_csv or config.TICKETS_CSV_PATH)
    results_dir = Path(results_dir or config.EVAL_RESULTS_DIR)
    model_path = Path(model_path or config.FAULT_MODEL_PATH)
    meta_path = Path(meta_path or config.FAULT_MODEL_META_PATH)

    candidates = load_candidates()
    result = evaluate(load_tickets(tickets_csv), candidates, Embedder(embed_key), seed,
                      progress=lambda msg: print(f"- {msg}", flush=True))
    ranker = result.pop("ranker")
    kpi_ok = result["results"][MODEL_RANKER][0]["top3"] >= config.KPI_TOP3

    results_dir.mkdir(parents=True, exist_ok=True)
    name = config.FAULT_RESULTS_NAME
    (results_dir / f"{name}.md").write_text(render_report(result, kpi_ok), encoding="utf-8", newline="\n")
    (results_dir / f"{name}.json").write_text(
        json.dumps({**result, "kpi_top3_ok": kpi_ok, "seed": seed, "tickets_sha256": _file_digest(tickets_csv)},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    with open(results_dir / f"{name}.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["eval", "model", "group", "value", "n", "top1", "top3", "mrr"],
                                lineterminator="\n")
        writer.writeheader()
        tables = {"test": result["results"], **{f"cv_{k}": v for k, v in result["cv"].items()}}
        for eval_name, models in tables.items():
            for model, rows in models.items():
                writer.writerows({"eval": eval_name, "model": model, **row} for row in rows)

    model_path.parent.mkdir(parents=True, exist_ok=True)
    ranker.save_model(model_path)
    meta = {
        "features": RANKER_FEATURES,
        "embedding_model": embed_key,
        "embedding_model_name": config.EMBED_MODELS[embed_key],
        "params": result["params"],
        "candidates": [c.method for c in candidates],
        "defect_counts": result["defect_counts"],  # 추론 시 past_defects (학습 분할 기준)
        "seed": seed,
        "tickets_sha256": _file_digest(tickets_csv),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {
        "overall": {n: rows[0] for n, rows in result["results"].items()},
        "cv_overall": {k: {n: rows[0] for n, rows in v.items()} for k, v in result["cv"].items()},
        "params": result["params"],
        "kpi_top3_ok": kpi_ok,
        "report": str(results_dir / f"{name}.md"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="결함 위치 모델 3종 학습·비교 (PRD F3-1, F3-3)")
    parser.add_argument("--embed", default=config.EMBED_MODEL, choices=list(config.EMBED_MODELS))
    args = parser.parse_args()
    print(json.dumps(run(embed_key=args.embed), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
