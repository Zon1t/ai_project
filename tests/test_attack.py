"""공격 (F5-3, F4-3): property_attack, 참조 구현 비교·분류, 발견 확인, 공격 메모리, S1 불완전 패치."""

import json
from pathlib import Path

import pytest

from sr_agent import config
from sr_agent.tools.attack import attack_memory, compare_versions, property_attack, run_diff_json
from sr_agent.tools.code import apply_patch
from sr_agent.tools.workspace import prepare_workspace
from sr_agent.workflow.attack import verify_finding
from sr_agent.workflow.judge import attack_findings_check, reference_check
from sr_agent.workflow.state import AttackFinding, AttackReport, JudgeSpec, ReferenceImpl, VerifiedFinding

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


# ---------------- 판정: 공격 입력 재실행 (attack_findings_rechecked) ----------------

def _report(*findings: tuple[str, str, str]) -> AttackReport:
    """(kind, method, input_json) 목록 → 공격 결과. 확인 여부(verified)와 LLM 기대값은 판정에 쓰이지 않는다."""
    return AttackReport(findings=[
        VerifiedFinding(kind=k, method=m, input_json=i, expected_json="0", rule_id="", explanation="")
        for k, m, i in findings])


def test_attack_findings_rechecked_against_reference(bug_ws):
    """공격 입력 전부를 정답과 비교한다. 분류는 LLM 라벨이 아니라 원본·정답 비교로 정한다."""
    attack = _report(("regression", "calc.payable", "[100, 300]"),  # LLM은 회귀라 했지만 원본도 틀림 → 미달
                     ("under_fix", "calc.payable", "[500, 100]"),  # 원본·정답 모두 400 → 차이 없음
                     ("under_fix", "calc.settle", "[100, 300]"))  # 호출부 경로도 실행
    check = attack_findings_check(bug_ws, attack, REF, "reference")  # 수정 전 (원본 = 수정본)
    assert check.passed is False
    assert {k: v for k, v in check.data.items() if k not in ("failed_inputs", "failed_keys")} == {
        "inputs": 3, "mismatches": 2, "under_fix": 2, "regression": 0, "skipped": 0}
    assert "예(미달)" in check.detail and "정답 " not in check.detail.split("예(")[1]  # 정답 출력은 넣지 않음
    failed = check.data["failed_inputs"]  # 재시도 입력용: 입력·분류·수정본·원본만 (정답 출력 없음)
    assert [(f["method"], f["kind"]) for f in failed] == [("calc.payable", "under_fix"), ("calc.settle", "under_fix")]
    assert all(set(f) == {"method", "input", "kind", "patched", "original"} for f in failed)

    assert apply_patch(bug_ws, "calc.payable", GOOD)["applied"]
    assert attack_findings_check(bug_ws, attack, REF, "reference").passed is True


def test_attack_findings_rechecked_classifies_regression(bug_ws):
    """결함 영역 밖(원본 = 정답)에서 수정본이 다르면 회귀."""
    patch = "def payable(amount, discount):\n    return 0 if amount >= 1000 else max(0, amount - discount)\n"
    assert apply_patch(bug_ws, "calc.payable", patch)["applied"]
    check = attack_findings_check(bug_ws, _report(("under_fix", "calc.payable", "[2000, 100]")), REF, "reference")
    assert check.passed is False and check.data["regression"] == 1 and "예(회귀)" in check.detail


def test_attack_findings_rechecked_skips_and_unevaluated(bug_ws):
    assert attack_findings_check(bug_ws, None, REF, "reference").passed is None  # 공격 기록 없음
    no_ref = JudgeSpec(method="calc.payable", strategy=INTS)
    attack = _report(("under_fix", "calc.payable", "[100, 300]"))
    assert attack_findings_check(bug_ws, attack, no_ref, "reference").passed is None  # 정답 구현 없음
    bad = _report(("under_fix", "calc.payable", "not json"), ("under_fix", "calc.payable", '{"x": 1}'),
                  ("under_fix", "calc.missing", "[1, 2]"))
    check = attack_findings_check(bug_ws, bad, REF, "reference")
    assert check.passed is None and check.data == {"inputs": 0, "skipped": 3}


def test_attack_findings_rechecked_without_hypothesis_strategy(tmp_path: Path, s1_spec):
    """E2E처럼 입력 생성 규칙이 없어도, 정답 구현만 있으면 공격 입력으로 불완전 패치를 잡는다."""
    spec = JudgeSpec(method=s1_spec.method, reference=s1_spec.reference)
    ws = prepare_workspace("SR-S1", runs_dir=tmp_path)
    assert apply_patch(ws, "discount.apply_coupon", S1_PATCHES["A_floor_only"])["applied"]
    # 주문 100,000원에 90,000원 쿠폰 뒤 20,000원 쿠폰(최소 100,000원): 정답 10,000원, A는 0원
    coupons = [{"type": "FIXED", "value": 90000, "min_order": 100000},
               {"type": "FIXED", "value": 20000, "min_order": 100000}]
    attack = _report(("under_fix", "discount.apply_coupons", json.dumps([100000, coupons])))
    assert reference_check(ws, spec).passed is None
    check = attack_findings_check(ws, attack, spec, "reference")
    assert check.passed is False and check.data["under_fix"] == 1, check.detail
