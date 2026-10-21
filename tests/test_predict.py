"""결함 위치 예측 도구 (F3-2): 반환 형식, SHAP 근거, 스택트레이스 반영, 입력 가드. 작은 합성 모델로 검사한다."""

import json

import numpy as np
import pytest

from sr_agent import config
from sr_agent.ml.features import FEATURES
from sr_agent.ml.train import RANKER_FEATURES, train_ranker
from sr_agent.tools import predict as predict_mod
from sr_agent.tools.predict import FEATURE_LABELS, FaultLocator, predict_fault_location
from tests.test_ml import _cand, _fake_embedder

CANDS = [_cand(f"m{i % 3}.f{i}", f"def f{i}():\n    return {i}") for i in range(6)]


@pytest.fixture
def locator(tmp_path):
    """스택트레이스 마지막 프레임이 정답인 데이터로 학습한 작은 모델."""
    rng = np.random.RandomState(0)
    X = rng.rand(30, len(CANDS), len(FEATURES)).astype(np.float32)
    X[:, :, FEATURES.index("trace_last")] = 0
    y = np.zeros((30, len(CANDS)), dtype=np.int32)
    for i in range(30):
        j = i % len(CANDS)
        X[i, j, FEATURES.index("trace_last")] = 1
        y[i, j] = 1
    ranker = train_ranker(X, y, {"max_depth": 2, "n_estimators": 30, "learning_rate": 0.3})
    model, meta = tmp_path / "model.json", tmp_path / "meta.json"
    ranker.save_model(model)
    meta.write_text(json.dumps({"features": RANKER_FEATURES, "embedding_model": "bge-m3",
                                "candidates": [c.method for c in CANDS]}), encoding="utf-8")
    return FaultLocator(model, meta, CANDS, _fake_embedder("bge-m3", tmp_path))


TRACE = 'Traceback (most recent call last):\n  File "order_settlement/m1.py", line 2, in f4\nValueError: x'


def test_predict_format_and_reasons(locator):
    result = locator.predict("쿠폰 금액이 이상합니다", top_k=3)
    assert len(result) == 3
    assert [r["score"] for r in result] == sorted((r["score"] for r in result), reverse=True)
    for r in result:
        assert r["method"] in {c.method for c in CANDS}
        assert len(r["reasons"]) == config.PREDICT_REASONS
        contributions = [x["contribution"] for x in r["reasons"]]
        assert contributions == sorted(contributions, reverse=True)
        for reason in r["reasons"]:
            assert reason["feature"] in RANKER_FEATURES and reason["label"] == FEATURE_LABELS[reason["feature"]]
    json.dumps(result)  # JSON 직렬화 가능


def test_traceback_in_ticket_moves_frame_to_top(locator):
    (top,) = locator.predict("결제 중 오류가 납니다\n" + TRACE, top_k=1)
    assert top["method"] == "m1.f4"
    assert top["reasons"][0]["feature"] == "trace_last" and top["reasons"][0]["value"] == 1.0


def test_shap_contributions_explain_score(locator):
    X = locator.features_for("아무 증상")
    contributions = np.asarray(locator.explainer.shap_values(X))
    assert np.allclose(contributions.sum(axis=1) + locator.explainer.expected_value, locator.ranker.predict(X),
                       atol=1e-4)


def test_candidate_change_still_predicts_and_marks_new_functions(locator, tmp_path):
    """주 모델은 함수 정체와 무관한 피처만 써서, 함수가 추가·삭제된 코드에도 예측한다."""
    added = _cand("m9.brand_new", "def brand_new():\n    return 99")
    loc = FaultLocator(tmp_path / "model.json", tmp_path / "meta.json", [*CANDS[:-1], added],
                       _fake_embedder("bge-m3", tmp_path))
    result = loc.predict("결제 중 오류가 납니다", top_k=6)
    assert {r["method"] for r in result} == {c.method for c in [*CANDS[:-1], added]}
    assert [r["method"] for r in result if r.get("new_function")] == ["m9.brand_new"]


def test_candidate_mismatch_requires_retraining_with_past_defects(locator, tmp_path):
    """함수별 과거 결함 수를 쓰는 모델은 함수 목록이 바뀌면 재학습을 요구한다."""
    meta = tmp_path / "meta.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    meta.write_text(json.dumps({**data, "features": [*data["features"], "past_defects"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="재학습"):
        FaultLocator(tmp_path / "model.json", meta, CANDS[:-1], _fake_embedder("bge-m3", tmp_path))


@pytest.mark.parametrize(("text", "top_k"), [("", 3), ("   ", 3), (None, 3), ("증상", 0), ("증상", 11),
                                             ("증상", True), ("증상", "3"), ("가" * 4001, 3)])
def test_tool_input_guard(text, top_k):
    result = predict_fault_location(text, top_k)
    assert set(result) == {"error"}


def test_tool_uses_shared_locator_and_returns_error_dict(locator, monkeypatch):
    monkeypatch.setattr(predict_mod, "_locator", locator)
    assert len(predict_fault_location("쿠폰 금액 오류", 2)) == 2

    def broken(text, top_k):
        raise RuntimeError("모델 파일 손상")
    monkeypatch.setattr(locator, "predict", broken)
    assert predict_fault_location("쿠폰 금액 오류", 2) == {"error": "RuntimeError: 모델 파일 손상"}
