"""결함 위치 모델 3종 학습·비교 (PRD F3-1, F3-3).

① TF-IDF 단독 ② 임베딩 유사도 단독 ③ XGBoost Ranker 혼합형.
학습은 train, 평가는 test 분할로 한다. holdout(E2E용)은 쓰지 않는다.
임베딩 모델은 학습 분할 MRR로 고른다 (평가 분할로 고르면 선택 자체가 평가에 샌다).
같은 데이터·시드면 같은 결과가 나온다.

실행: python -m sr_agent.ml.train  (PYTHONPATH=src)
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from xgboost import XGBRanker

from sr_agent import config
from sr_agent.ml.corpus import Candidate, load_candidates, load_tickets, ticket_text
from sr_agent.ml.features import FEATURES, Embedder, build_features, defect_counts, embedding_scores, tfidf_scores
from sr_agent.ml.metrics import breakdown, ranks, summarize

MODEL_TFIDF = "① TF-IDF"
MODEL_EMB = "② 임베딩"
MODEL_RANKER = "③ 혼합형 (XGBoost Ranker)"


def _flat(X: np.ndarray) -> np.ndarray:
    return X.reshape(-1, X.shape[-1])


def train_ranker(X: np.ndarray, y: np.ndarray, seed: int = config.SEED) -> XGBRanker:
    """X [티켓, 후보, 피처], y [티켓, 후보]. 티켓 하나가 쿼리 하나."""
    qid = np.repeat(np.arange(X.shape[0]), X.shape[1])
    ranker = XGBRanker(**config.RANKER_PARAMS, random_state=seed)
    ranker.fit(_flat(X), y.reshape(-1), qid=qid)
    return ranker


def predict_scores(ranker: XGBRanker, X: np.ndarray) -> np.ndarray:
    return ranker.predict(_flat(X)).reshape(X.shape[0], X.shape[1])


def evaluate(
    tickets: list[dict],
    candidates: list[Candidate],
    embedders: dict[str, Embedder],
    seed: int = config.SEED,
) -> dict:
    """세 모델을 학습·평가한다. 반환: 지표, 임베딩 비교, 피처 중요도, 학습된 ranker."""
    methods = {c.method for c in candidates}
    if unknown := sorted({t["fixed_method"] for t in tickets} - methods):
        raise ValueError(f"후보에 없는 정답 함수: {unknown}")
    train = [t for t in tickets if t["split"] == "train"]
    test = [t for t in tickets if t["split"] == "test"]
    texts = {"train": [ticket_text(t) for t in train], "test": [ticket_text(t) for t in test]}
    counts = defect_counts(train)

    tfidf = {s: tfidf_scores(texts[s], candidates) for s in texts}
    emb = {k: {s: embedding_scores(e, texts[s], candidates) for s in texts} for k, e in embedders.items()}
    X, y = {}, {}
    _, y["train"] = build_features(train, candidates, tfidf["train"], tfidf["train"], counts)
    _, y["test"] = build_features(test, candidates, tfidf["test"], tfidf["test"], counts)

    embed_compare = {k: {s: summarize(ranks(emb[k][s], y[s])) for s in texts} for k in emb}
    best = max(embed_compare, key=lambda k: embed_compare[k]["train"]["mrr"])
    for s, rows in (("train", train), ("test", test)):
        X[s], y[s] = build_features(rows, candidates, tfidf[s], emb[best][s], counts)

    ranker = train_ranker(X["train"], y["train"], seed)
    scores = {
        MODEL_TFIDF: tfidf["test"],
        f"{MODEL_EMB} ({best})": emb[best]["test"],
        MODEL_RANKER: predict_scores(ranker, X["test"]),
    }
    return {
        "counts": {"candidates": len(candidates), "train_tickets": len(train), "test_tickets": len(test),
                   "train_mutants": len({t["mutant_id"] for t in train}),
                   "test_mutants": len({t["mutant_id"] for t in test})},
        "embedding_model": best,
        "embedding_compare": embed_compare,
        "results": {name: breakdown(ranks(s, y["test"]), test) for name, s in scores.items()},
        "train_results": {MODEL_RANKER: summarize(ranks(predict_scores(ranker, X["train"]), y["train"]))},
        "feature_importance": dict(zip(FEATURES, (round(float(v), 4) for v in ranker.feature_importances_),
                                       strict=True)),
        "defect_counts": dict(sorted(counts.items())),
        "ranker": ranker,
    }


def _fmt(v) -> str:
    return "-" if v is None else f"{v:.3f}"


def render_report(result: dict, kpi_ok: bool) -> str:
    """eval/results/fault_localization.md 본문."""
    names = list(result["results"])
    overall = {n: result["results"][n][0] for n in names}
    lines = [
        "# 결함 위치 예측: 모델 3종 비교 (F3-1, F3-3)",
        "",
        f"- 데이터: 평가(test) 티켓 {result['counts']['test_tickets']}건 / 결함 {result['counts']['test_mutants']}개, "
        f"학습(train) 티켓 {result['counts']['train_tickets']}건 / 결함 {result['counts']['train_mutants']}개, "
        f"후보 함수 {result['counts']['candidates']}개",
        f"- 임베딩 모델: `{result['embedding_model']}` (학습 분할 MRR로 선택), 시드 {config.SEED}",
        "- 동점은 불리하게 센다 (정답과 같은 점수인 후보가 모두 앞에 있다고 봄)",
        "",
        "## 비교표 (평가 분할 전체)",
        "",
        "| 모델 | Top-1 | Top-3 | MRR |",
        "|---|---|---|---|",
    ]
    lines += [f"| {n} | {_fmt(o['top1'])} | {_fmt(o['top3'])} | {_fmt(o['mrr'])} |" for n, o in overall.items()]
    top3 = overall[MODEL_RANKER]["top3"]
    lines += ["", f"**KPI (혼합형 Top-3 ≥ {config.KPI_TOP3:.2f})**: {top3:.3f} → {'달성' if kpi_ok else '미달'}", ""]

    lines += ["## 나눠 보기", "", "| 구분 | 값 | n | " + " | ".join(f"{n} Top-1 / Top-3 / MRR" for n in names) + " |",
              "|---|---|---|" + "---|" * len(names)]
    for i, row in enumerate(result["results"][names[0]]):
        cells = []
        for n in names:
            r = result["results"][n][i]
            cells.append(f"{_fmt(r['top1'])} / {_fmt(r['top3'])} / {_fmt(r['mrr'])}")
        lines.append(f"| {row['group']} | {row['value']} | {row['n']} | " + " | ".join(cells) + " |")

    lines += ["", "## 임베딩 모델 비교 (② 단독)", "", "| 모델 | 분할 | Top-1 | Top-3 | MRR |", "|---|---|---|---|---|"]
    for k, by_split in result["embedding_compare"].items():
        for s, m in by_split.items():
            lines.append(f"| {k} | {s} | {_fmt(m['top1'])} | {_fmt(m['top3'])} | {_fmt(m['mrr'])} |")

    lines += ["", "## 혼합형 피처 중요도 (gain)", "", "| 피처 | 중요도 |", "|---|---|"]
    lines += [f"| {f} | {v:.3f} |" for f, v in sorted(result["feature_importance"].items(), key=lambda x: -x[1])]
    tr = result["train_results"][MODEL_RANKER]
    lines += ["", f"참고: 혼합형의 학습 분할 성능 Top-1 {_fmt(tr['top1'])} / Top-3 {_fmt(tr['top3'])} / "
              f"MRR {_fmt(tr['mrr'])} (평가 분할과의 차이로 과적합 정도를 본다)", ""]
    return "\n".join(lines)


def _file_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(
    tickets_csv: Path | None = None,
    embed_keys: tuple[str, ...] = tuple(config.EMBED_MODELS),
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
    result = evaluate(load_tickets(tickets_csv), candidates, {k: Embedder(k) for k in embed_keys}, seed)
    ranker = result.pop("ranker")
    kpi_ok = result["results"][MODEL_RANKER][0]["top3"] >= config.KPI_TOP3

    results_dir.mkdir(parents=True, exist_ok=True)
    name = config.FAULT_RESULTS_NAME
    (results_dir / f"{name}.md").write_text(render_report(result, kpi_ok), encoding="utf-8", newline="\n")
    (results_dir / f"{name}.json").write_text(
        json.dumps({**result, "kpi_top3_ok": kpi_ok, "seed": seed, "tickets_sha256": _file_digest(tickets_csv)},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    with open(results_dir / f"{name}.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["model", "group", "value", "n", "top1", "top3", "mrr"],
                                lineterminator="\n")
        writer.writeheader()
        for model, rows in result["results"].items():
            writer.writerows({"model": model, **row} for row in rows)

    model_path.parent.mkdir(parents=True, exist_ok=True)
    ranker.save_model(model_path)
    meta = {
        "features": FEATURES,
        "embedding_model": result["embedding_model"],
        "embedding_model_name": config.EMBED_MODELS[result["embedding_model"]],
        "candidates": [c.method for c in candidates],
        "defect_counts": result["defect_counts"],  # 추론 시 past_defects (학습 분할 기준)
        "seed": seed,
        "tickets_sha256": _file_digest(tickets_csv),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {
        "overall": {n: rows[0] for n, rows in result["results"].items()},
        "embedding_model": result["embedding_model"],
        "kpi_top3_ok": kpi_ok,
        "report": str(results_dir / f"{name}.md"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="결함 위치 모델 3종 학습·비교 (PRD F3-1, F3-3)")
    parser.add_argument("--embed", nargs="+", default=list(config.EMBED_MODELS), choices=list(config.EMBED_MODELS))
    args = parser.parse_args()
    print(json.dumps(run(embed_keys=tuple(args.embed)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
