"""데이터 분할 (F2-4)과 누수 검사."""

import csv
import json
from collections import Counter

import pytest

from sr_agent import config
from sr_agent.datagen.split import assign_splits, build_rows, check_leakage, run_split

DEMO = "discount.apply_coupon"
OPERATORS = ["ROR", "AOR", "NEG", "CRP", "SDL"]
EXC_TRACE = (
    "    def f():\n>       return a - b\nE       TypeError: bad operand\n\n"
    "src\\order_settlement\\m0.py:3: TypeError"
)


def _mutants() -> list[dict]:
    """함수 30개 × 결함 4개 + 시연 함수 결함 5개 = 125개."""
    ms = []
    for i in range(30):
        for j in range(4):
            ms.append({
                "mutant_id": f"MUT-{i:04d}{j:04d}", "method": f"m{i % 6}.f{i}", "operator": OPERATORS[(i + j) % 5],
                "inject_type": "ast", "n_failed": 1,
                "traces": [{"trace": EXC_TRACE if j == 0 else "E   assert 1 == 2"}],
            })
    for j in range(5):
        ms.append({"mutant_id": f"MUT-DEMO{j:04d}", "method": DEMO, "operator": "ROR", "inject_type": "ast",
                   "n_failed": 1, "traces": [{"trace": "E   assert 1 == 2"}]})
    return ms


def _drafts(mutants: list[dict]) -> list[dict]:
    pair = {"ticket_a": {"title": "금액 오류", "body": "금액이 이상합니다."},
            "ticket_b": {"title": "금액 불일치", "body": "기대 9만 원, 실제 8만 원입니다."}}
    return [{"mutant_id": m["mutant_id"], "status": "ok", **pair} for m in mutants]


def _rows():
    ms = _mutants()
    return build_rows(_drafts(ms), ms, assign_splits(ms))


def test_assign_splits_rules():
    ms = _mutants()
    splits = assign_splits(ms)
    assert set(splits) == {m["mutant_id"] for m in ms}
    method = {m["mutant_id"]: m["method"] for m in ms}

    holdout = [mid for mid, s in splits.items() if s == "holdout"]
    assert len(holdout) == config.E2E_HOLDOUT_SIZE
    assert len({method[mid] for mid in holdout}) == config.E2E_HOLDOUT_SIZE  # 함수마다 하나
    assert all(splits[m["mutant_id"]] == "train" for m in ms if m["method"] == DEMO)

    rest = [mid for mid in splits if method[mid] != DEMO and splits[mid] != "holdout"]
    n_test = sum(splits[mid] == "test" for mid in rest)
    assert abs(n_test / len(rest) - config.SPLIT_TEST_RATIO) < 0.03


def test_assign_splits_keeps_operator_ratio():
    ms = _mutants()
    splits = assign_splits(ms)
    op = {m["mutant_id"]: m["operator"] for m in ms}
    for operator in OPERATORS:
        group = [mid for mid, s in splits.items() if op[mid] == operator and s in ("train", "test")
                 and not mid.startswith("MUT-DEMO")]
        assert abs(sum(splits[mid] == "test" for mid in group) - len(group) * config.SPLIT_TEST_RATIO) <= 1


def test_assign_splits_deterministic_and_order_independent():
    ms = _mutants()
    assert assign_splits(ms, seed=1) == assign_splits(list(reversed(ms)), seed=1)
    assert assign_splits(ms, seed=1) != assign_splits(ms, seed=2)


def test_holdout_needs_enough_methods():
    with pytest.raises(ValueError, match="홀드아웃"):
        assign_splits(_mutants()[:40])  # 함수 10개뿐


def test_rows_two_tickets_per_mutant_same_split_and_stacktrace_only_on_b():
    rows = _rows()
    assert len(rows) == 2 * len(_mutants())
    per_mutant = Counter(r["mutant_id"] for r in rows)
    assert set(per_mutant.values()) == {2}
    assert all(not r["has_stacktrace"] for r in rows if r["reporter"] == "운영")
    with_tb = [r for r in rows if r["has_stacktrace"]]
    assert len(with_tb) == 30 and all(r["ticket_id"].endswith("-B") for r in with_tb)
    assert with_tb[0]["stacktrace"].startswith("Traceback (most recent call last):")
    assert check_leakage(rows) == []


@pytest.mark.parametrize(("mutate", "message"), [
    (lambda rows: rows[0].update(split="test" if rows[0]["split"] != "test" else "train"), "여러 분할"),
    (lambda rows: next(r for r in rows if r["fixed_method"] == DEMO).update(split="test"), "시연 함수"),
    (lambda rows: rows[0].update(body="apply_coupon 오류 확인 바랍니다"), "식별자"),
    (lambda rows: rows[1].update(ticket_id=rows[0]["ticket_id"]), "중복"),
    (lambda rows: rows[0].update(split="dev"), "알 수 없는 분할"),
])
def test_check_leakage_detects(mutate, message):
    rows = _rows()
    mutate(rows)
    assert any(message in p for p in check_leakage(rows))


def test_run_split_writes_csv_and_refuses_leaky_data(tmp_path):
    ms = _mutants()
    mutants_path, drafts_path = tmp_path / "mutants.jsonl", tmp_path / "drafts.jsonl"
    mutants_path.write_text("\n".join(json.dumps(m) for m in ms), encoding="utf-8")
    drafts = _drafts(ms) + [{"mutant_id": "MUT-X", "status": "rejected"}]
    drafts_path.write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in drafts), encoding="utf-8")

    out = dict(drafts_path=drafts_path, mutants_path=mutants_path,
               csv_path=tmp_path / "tickets.csv", split_path=tmp_path / "split.json")
    summary = run_split(**out)
    assert summary["counts"]["holdout"]["mutants"] == 20 and summary["counts"]["holdout"]["tickets"] == 40
    with open(out["csv_path"], encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2 * len(ms) and check_leakage(rows) == []
    saved = json.loads(out["split_path"].read_text(encoding="utf-8"))
    assert sum(len(v) for v in saved["mutants"].values()) == len(ms)

    leaky = _drafts(ms)
    leaky[0]["ticket_a"]["body"] = "f0 함수에서 오류"  # 정답 함수명
    drafts_path.write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in leaky), encoding="utf-8")
    out["csv_path"] = tmp_path / "leaky.csv"
    assert run_split(**out)["error"] == "누수 검사 실패"
    assert not out["csv_path"].exists()


@pytest.mark.skipif(not config.TICKETS_CSV_PATH.is_file(), reason="tickets.csv 생성 전")
def test_real_dataset_has_no_leakage():
    """실제 data/tickets/tickets.csv: 누수 없음, 홀드아웃 결함 수, 시연 함수는 학습에만."""
    with open(config.TICKETS_CSV_PATH, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert check_leakage(rows) == []
    holdout = {r["mutant_id"] for r in rows if r["split"] == "holdout"}
    assert len(holdout) == config.E2E_HOLDOUT_SIZE
    split = json.loads(config.SPLIT_PATH.read_text(encoding="utf-8"))
    assert set(split["mutants"]["holdout"]) == holdout
