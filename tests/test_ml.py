"""결함 위치 모델 (F3-1, F3-3): 후보·피처·누수 방지·지표·학습 파이프라인. 임베딩은 가짜로 대체한다."""

import ast
import hashlib
import json

import numpy as np
import pytest

from sr_agent import config
from sr_agent.ml.corpus import Candidate, complexity, load_candidates, ticket_text
from sr_agent.ml.features import FEATURES, Embedder, build_features, defect_counts, tfidf_scores, trace_frames
from sr_agent.ml.metrics import breakdown, ranks, summarize
from sr_agent.ml.train import MODEL_RANKER, evaluate

TRACE = (
    "Traceback (most recent call last):\n"
    '  File "order_settlement/settlement.py", line 37, in calc_due_date\n'
    "    month_end = date(...)\n"
    '  File "order_settlement/date_utils.py", line 12, in last_day_of_month\n'
    "    raise ValueError\n"
    "ValueError: day is out of range for month"
)


def _cand(method: str, source: str = "def f():\n    return 1") -> Candidate:
    module, name = method.split(".")
    return Candidate(method, module, name, source, f"{module} 모듈", source.count("\n") + 1, 1)


def _ticket(mid: str, method: str, split: str, stacktrace: str = "", **kw) -> dict:
    return {"ticket_id": f"T-{mid}", "mutant_id": mid, "fixed_method": method, "split": split,
            "title": kw.get("title", "오류"), "body": kw.get("body", "증상"), "stacktrace": stacktrace,
            "has_stacktrace": bool(stacktrace), "inject_type": "ast", "operator": kw.get("operator", "ROR")}


def test_complexity_counts_branches_and_bool_operands():
    tree = ast.parse(
        "def f(a, b, c):\n    if a and b and c:\n        return [x for x in a]\n    while b:\n        pass\n"
    )
    assert complexity(tree.body[0]) == 1 + 1 + 2 + 1 + 1  # 기본 + if + and 2개 + 컴프리헨션 + while


def test_real_candidates_cover_all_mutants():
    cands = load_candidates()
    methods = {c.method for c in cands}
    assert len(cands) >= 100 and "discount.apply_coupon" in methods
    lines = config.MUTANTS_PATH.read_text(encoding="utf-8").splitlines()
    mutant_methods = {json.loads(line)["method"] for line in lines}
    assert mutant_methods <= methods
    c = next(c for c in cands if c.method == "discount.apply_coupon")
    assert c.source.startswith("def apply_coupon") and "할인 계산" in c.document


def test_trace_frames():
    assert trace_frames(TRACE) == [("settlement", "calc_due_date"), ("date_utils", "last_day_of_month")]
    assert trace_frames("") == []


def test_defect_counts_by_mutant_and_train_only():
    tickets = [_ticket("M1", "a.f", "train"), {**_ticket("M1", "a.f", "train"), "ticket_id": "T-M1-B"},
               _ticket("M2", "a.f", "train"), _ticket("M3", "b.g", "test"), _ticket("M4", "b.g", "holdout")]
    assert defect_counts(tickets) == {"a.f": 2}  # 같은 결함의 티켓 2건은 1개, test·holdout 제외


def test_build_features_past_defects_excludes_own_mutant_and_trace_flags():
    cands = [_cand("settlement.calc_due_date"), _cand("date_utils.last_day_of_month"), _cand("order.get_order")]
    tickets = [_ticket("M1", "date_utils.last_day_of_month", "train", TRACE),
               _ticket("M9", "date_utils.last_day_of_month", "test")]
    counts = {"date_utils.last_day_of_month": 3}
    zeros = np.zeros((2, 3))
    X, y = build_features(tickets, cands, zeros, zeros, counts)
    past = X[:, :, FEATURES.index("past_defects")]
    assert past[0].tolist() == [0, 2, 0]  # 학습 티켓: 자기 결함 1개 제외
    assert past[1].tolist() == [0, 3, 0]  # 평가 티켓: 학습 분할 개수 그대로
    assert y.tolist() == [[0, 1, 0], [0, 1, 0]]
    assert X[0, :, FEATURES.index("trace_in")].tolist() == [1, 1, 0]
    assert X[0, :, FEATURES.index("trace_last")].tolist() == [0, 1, 0]
    assert X[0, :, FEATURES.index("trace_module")].tolist() == [1, 1, 0]
    assert X[1, :, FEATURES.index("trace_in")].tolist() == [0, 0, 0]


def test_tfidf_prefers_matching_function():
    cands = [_cand("discount.apply_coupon", 'def apply_coupon():\n    """쿠폰 적용 할인"""'),
             _cand("payment.refund", 'def refund():\n    """환불 수수료"""')]
    scores = tfidf_scores(["환불 수수료가 이상합니다", "쿠폰 할인 금액 오류"], cands)
    assert scores.shape == (2, 2)
    assert scores[0].argmax() == 1 and scores[1].argmax() == 0


def test_ranks_ties_are_pessimistic_and_summary():
    labels = np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0]])
    scores = np.array([[0.9, 0.1, 0.0], [0.5, 0.5, 0.5], [0.3, 0.2, 0.1]])
    r = ranks(scores, labels)
    assert r.tolist() == [1, 3, 2]
    assert summarize(r) == {"n": 3, "top1": 0.3333, "top3": 1.0, "mrr": round((1 + 1 / 3 + 1 / 2) / 3, 4)}


def test_breakdown_groups():
    tickets = [_ticket("M1", "a.f", "test", TRACE, operator="ROR"), _ticket("M2", "a.f", "test", operator="AOR")]
    rows = breakdown(np.array([1, 4]), tickets)
    by = {(r["group"], r["value"]): r for r in rows}
    assert by[("전체", "전체")]["n"] == 2
    assert by[("스택트레이스", "있음")]["top1"] == 1.0 and by[("스택트레이스", "없음")]["top3"] == 0.0
    assert ("주입 방식", "ast") in by and by[("연산자", "AOR")]["mrr"] == 0.25


class FakeModel:
    """텍스트 해시로 결정적인 벡터를 만든다. 호출 횟수를 센다."""

    def __init__(self):
        self.calls = 0

    def encode(self, texts, **kwargs):
        self.calls += 1
        vecs = np.array([np.frombuffer(hashlib.sha256(t.encode()).digest(), dtype=np.uint8)[:8] for t in texts],
                        dtype=np.float32) + 1
        return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def _fake_embedder(key: str, tmp_path) -> Embedder:
    e = Embedder(key, cache_dir=tmp_path)
    e._model = FakeModel()
    return e


def test_embedder_caches_vectors(tmp_path):
    e = _fake_embedder("bge-m3", tmp_path)
    first = e.encode(["a", "b"])
    assert np.allclose(np.linalg.norm(first, axis=1), 1)
    again = _fake_embedder("bge-m3", tmp_path)
    assert np.array_equal(again.encode(["b", "a"]), first[::-1])
    assert again._model.calls == 0  # 캐시에서 읽음


def _synthetic_dataset():
    """함수 12개. 학습 티켓은 스택트레이스로 정답이 드러나고, 평가 티켓 일부도 그렇다."""
    cands = [_cand(f"m{i % 3}.f{i}") for i in range(12)]
    tickets = []
    for i in range(12):
        method = cands[i].method
        tb = f'Traceback (most recent call last):\n  File "order_settlement/m{i % 3}.py", line 1, in f{i}\nE: x'
        for j in range(4):
            split = "test" if j == 3 else "train"
            tickets.append(_ticket(f"M{i}-{j}", method, split, tb if j % 2 == 0 or split == "test" else "",
                                   body=f"증상 {i}"))
    return cands, tickets


def test_evaluate_pipeline_is_deterministic_and_learns_trace(tmp_path, monkeypatch):
    cands, tickets = _synthetic_dataset()
    embedders = {"bge-m3": _fake_embedder("bge-m3", tmp_path), "unixcoder": _fake_embedder("unixcoder", tmp_path)}
    result = evaluate(tickets, cands, embedders, seed=1)
    assert set(result["results"]) == {"① TF-IDF", f"② 임베딩 ({result['embedding_model']})", MODEL_RANKER}
    overall = result["results"][MODEL_RANKER][0]
    assert overall["n"] == 12 and overall["top1"] == 1.0  # 평가 티켓 모두 traceback에 정답
    assert result["counts"]["train_mutants"] == 36 and result["counts"]["test_tickets"] == 12
    again = evaluate(tickets, cands, embedders, seed=1)
    again.pop("ranker"), result.pop("ranker")
    assert again == result


def test_evaluate_rejects_unknown_method(tmp_path):
    cands, tickets = _synthetic_dataset()
    tickets[0]["fixed_method"] = "zz.unknown"
    with pytest.raises(ValueError, match="후보에 없는"):
        evaluate(tickets, cands, {"bge-m3": _fake_embedder("bge-m3", tmp_path)})


def test_ticket_text_includes_stacktrace_only_when_present():
    assert ticket_text(_ticket("M1", "a.f", "train", title="제목", body="본문")) == "제목\n본문"
    assert ticket_text(_ticket("M1", "a.f", "train", TRACE)).endswith("day is out of range for month")
