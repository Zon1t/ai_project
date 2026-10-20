"""공격 (F5-3, F4-3): property_attack, 참조 구현 비교·분류, 발견 확인, 공격 메모리, S1 불완전 패치."""

import json
from pathlib import Path

import pytest

from sr_agent import config
from sr_agent.tools.attack import attack_memory, compare_versions, property_attack, run_diff_json
from sr_agent.tools.code import apply_patch
from sr_agent.tools.workspace import prepare_workspace
from sr_agent.workflow.attack import verify_finding
from sr_agent.workflow.judge import reference_check
from sr_agent.workflow.state import AttackFinding, JudgeSpec, ReferenceImpl

INTS = {"args": [{"type": "integer", "min": 0, "max": 1000}, {"type": "integer", "min": 0, "max": 1000}]}
GOOD = "def payable(amount, discount):\n    return max(0, amount - discount)\n"
REF = JudgeSpec(method="calc.payable", strategy=INTS, reference=ReferenceImpl(method="calc.payable", code=GOOD))


# ---------------- property_attack ----------------

def test_property_attack_finds_and_shrinks_difference(bug_ws):
    apply_patch(bug_ws, "calc.payable", GOOD)
    result = property_attack(bug_ws, "calc.payable", INTS, 100)
    assert result["examples"] == 100 and result["mismatches"] > 0
    minimal = result["minimal"]
    assert minimal["input"] == {"args": [0, 1], "kwargs": {}}  # Hypothesis가 줄인 최소 사례
    assert minimal["original"] == {"ok": True, "value": -1} and minimal["patched"] == {"ok": True, "value": 0}
    assert len(result["cases"]) <= config.PROPERTY_CASES_REPORTED and not result["cases"][0]["same"]


def test_property_attack_no_change_and_deterministic(bug_ws):
    first = property_attack(bug_ws, "calc.payable", json.dumps(INTS), 50)  # JSON 문자열도 받는다
    assert first["mismatches"] == 0 and first["minimal"] is None
    apply_patch(bug_ws, "calc.payable", GOOD)
    assert property_attack(bug_ws, "calc.payable", INTS, 50) == property_attack(bug_ws, "calc.payable", INTS, 50)


@pytest.mark.parametrize(("spec", "max_examples", "message"), [
    ("{bad", 10, "JSON"),
    ({"args": [{"type": "float"}]}, 10, "지원하지 않는 전략"),
    ({"args": [{"min": 1}]}, 10, "type"),
    ({"positional": []}, 10, "args"),
    (INTS, 0, "max_examples"),
    (INTS, config.PROPERTY_MAX_EXAMPLES + 1, "max_examples"),
])
def test_property_attack_rejects_bad_spec(bug_ws, spec, max_examples, message):
    assert message in property_attack(bug_ws, "calc.payable", spec, max_examples)["error"]


def test_strategy_types_cover_dates_dicts_and_lists(bug_ws):
    spec = {"args": [{"type": "list", "max_size": 2, "items": {"type": "dict", "fields": {
        "d": {"type": "date", "min": "2024-01-01", "max": "2024-12-31"},
        "t": {"type": "one_of", "options": [{"type": "none"}, {"type": "sampled", "values": ["A", "B"]}]}}}},
        {"type": "boolean"}]}
    result = property_attack(bug_ws, "calc.payable", spec, 20)
    assert result["examples"] == 20  # 함수가 예외를 내도 결과로 기록되고, 두 버전이 같으므로 불일치 없음
    assert result["mismatches"] == 0


def test_run_diff_json(bug_ws):
    apply_patch(bug_ws, "calc.payable", GOOD)
    rows = run_diff_json(bug_ws, "calc.payable", "[[100, 300], {\"args\": [500, 100]}]")
    assert [r["same"] for r in rows] == [False, True]
    assert "JSON" in run_diff_json(bug_ws, "calc.payable", "[1,")["error"]


# ---------------- 참조 구현 비교 (판정) ----------------

def test_reference_check_classifies_under_fix_and_regression(bug_ws):
    assert reference_check(bug_ws, REF).passed is False  # 아직 안 고침 → 미달
    detail = reference_check(bug_ws, REF).detail
    assert "회귀 0" in detail and "최소 사례(미달)" in detail

    apply_patch(bug_ws, "calc.payable", GOOD)
    good = reference_check(bug_ws, REF)
    assert good.passed and "정답과 다름 0개" in good.detail

    apply_patch(bug_ws, "calc.payable", "def payable(amount, discount):\n    return max(0, amount - discount - 1)\n")
    bad = reference_check(bug_ws, REF)
    assert not bad.passed and "최소 사례(회귀)" in bad.detail  # 원본이 맞던 입력(할인 < 금액)에서 1원 틀림


def test_compare_versions_counts_kinds(bug_ws):
    src = config.TARGET_SRC_SUBDIR
    raw = compare_versions("calc.payable", INTS, {"original": bug_ws.original / src, "patched": bug_ws.work / src,
                                                   "reference": bug_ws.original / src},
                           "reference", "patched", 30, bug_ws.work)
    assert raw["mismatches"] == 0 and raw["under_fix"] == 0 and raw["regression"] == 0


def test_reference_with_bad_code_fails_check(bug_ws):
    bad = REF.model_copy(update={"reference": ReferenceImpl(method="calc.payable", code="def other():\n    pass\n")})
    check = reference_check(bug_ws, bad)
    assert check.passed is False and "참조 구현 적용 실패" in check.detail


# ---------------- 발견 확인 ----------------

def _finding(kind, inp, expected):
    return AttackFinding(kind=kind, method="calc.payable", input_json=inp, expected_json=expected, rule_id="R",
                         explanation="")


def test_verify_finding(bug_ws):
    under = verify_finding(bug_ws, _finding("under_fix", "[100, 300]", "0"))
    assert under.verified and under.patched == {"ok": True, "value": -200}  # 아직 안 고침
    apply_patch(bug_ws, "calc.payable", GOOD)
    assert not verify_finding(bug_ws, _finding("under_fix", "[100, 300]", "0")).verified
    assert verify_finding(bug_ws, _finding("regression", "[100, 300]", "-200")).verified  # 원본과 다름
    assert not verify_finding(bug_ws, _finding("regression", "[500, 100]", "400")).verified
    assert "JSON" in verify_finding(bug_ws, _finding("under_fix", "[1,", "0")).check
    exc = verify_finding(bug_ws, _finding("under_fix", "[\"a\", 1]", '{"$exception": "TypeError"}'))
    assert not exc.verified  # 기대대로 TypeError가 나므로 미달 아님


# ---------------- 공격 메모리 ----------------

def test_attack_memory_save_and_search(tmp_path):
    chroma = tmp_path / "chroma"
    rec = {"ticket_id": "SR-1", "method": "discount.apply_coupon", "kind": "under_fix",
           "input": [100000, []], "expected": 90000, "actual": 80000, "note": "경계값"}
    assert attack_memory("save", "defect", rec, chroma)[0]["saved"].startswith("ATK-")
    attack_memory("save", "defect", {**rec, "method": "order.calc_shipping_fee", "ticket_id": "SR-2"}, chroma)
    attack_memory("save", "security", {**rec, "ticket_id": "SR-3"}, chroma)
    attack_memory("save", "defect", rec, chroma)  # 같은 공격은 한 번만
    found = attack_memory("search", "defect", json.dumps({"method": "order.calc_shipping_fee"}), chroma)
    assert [r["method"] for r in found] == ["order.calc_shipping_fee", "discount.apply_coupon"]
    assert found[1] == {"method": "discount.apply_coupon", "kind": "under_fix", "input": [100000, []],
                        "expected": 90000, "actual": 80000, "note": "경계값", "ticket_id": "SR-1"}
    assert len(attack_memory("search", "security", None, chroma)) == 1


@pytest.mark.parametrize(("op", "attack_type", "record", "message"), [
    ("delete", "defect", None, "op"), ("search", "unknown", None, "attack_type"),
    ("save", "defect", {"method": "m"}, "필요한 필드"), ("save", "defect", "{bad", "JSON"),
    ("save", "defect", {"ticket_id": "T", "method": "m", "kind": "bug", "input": 1, "expected": 1, "actual": 1},
     "kind"),
])
def test_attack_memory_errors(tmp_path, op, attack_type, record, message):
    assert message in attack_memory(op, attack_type, record, tmp_path / "c")["error"]


# ---------------- S1: 일부러 불완전하게 만든 패치 ----------------

_HEAD = ("def apply_coupon(order_amount, coupon, current_amount=None):\n    if current_amount is None:\n"
         "        current_amount = order_amount\n")
_APPLY = "    discount = calc_coupon_discount(current_amount, coupon)\n"
S1_PATCHES = {
    "good": _HEAD + "    if not is_coupon_applicable(current_amount, coupon):\n        return current_amount\n"
            + _APPLY + "    return max(0, current_amount - discount)\n",
    "A_floor_only": _HEAD + "    if not is_coupon_applicable(order_amount, coupon):\n        return current_amount\n"
                    + _APPLY + "    return max(0, current_amount - discount)\n",
    "B_basis_only": _HEAD + "    if not is_coupon_applicable(current_amount, coupon):\n        return current_amount\n"
                    + _APPLY + "    return current_amount - discount\n",
    "C_boundary_regression": _HEAD + '    if current_amount <= coupon.get("min_order", 100000):\n'
                             "        return current_amount\n" + _APPLY
                             + "    return max(0, current_amount - discount)\n",
}


@pytest.fixture(scope="module")
def s1_spec():
    items = json.loads((config.DATA_DIR / "scenarios.json").read_text(encoding="utf-8"))
    return JudgeSpec(**next(i for i in items if i["ticket_id"] == "SR-2041")["judge"])


@pytest.mark.parametrize(("name", "passed", "kind"), [
    ("good", True, None), ("A_floor_only", False, "미달"), ("B_basis_only", False, "미달"),
    ("C_boundary_regression", False, "회귀"),
])
def test_s1_incomplete_patches_are_caught(tmp_path: Path, s1_spec, name, passed, kind):
    ws = prepare_workspace("SR-S1", runs_dir=tmp_path)
    assert apply_patch(ws, "discount.apply_coupon", S1_PATCHES[name])["applied"]
    check = reference_check(ws, s1_spec)
    assert check.passed is passed, check.detail
    if kind:
        assert f"최소 사례({kind})" in check.detail
