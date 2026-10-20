"""read_method (PRD 7.3): 작업본의 함수 소스와 위치."""

import pytest

from sr_agent.tools.code import read_method
from tests.conftest import replace_in, write


def test_read_method_returns_location_and_source(ws):
    assert read_method(ws, "calc.add") == "# src/order_settlement/calc.py:4-5\ndef add(a, b):\n    return a + b"
    assert read_method(ws, "order_settlement.calc.add").startswith("# src/order_settlement/calc.py:4-5")


def test_read_method_reads_work_copy(ws, work_calc):
    replace_in(work_calc, "return a + b", "return a - b")
    assert read_method(ws, "calc.add").endswith("return a - b")


def test_read_method_class_method_and_decorator(ws):
    write(ws.work / "src/order_settlement/svc.py",
          "import functools\n\n\nclass Svc:\n    @functools.cache\n    def run(self):\n        return 1\n")
    assert read_method(ws, "svc.Svc.run") == "# src/order_settlement/svc.py:5-7\n    @functools.cache\n" \
        "    def run(self):\n        return 1"


@pytest.mark.parametrize(("method", "message"), [
    ("add", "형식 오류"), ("calc.", "형식 오류"), ("../x.add", "형식 오류"), (None, "형식 오류"),
    ("nomod.add", "모듈 없음"), ("calc.nothing", "함수 없음"),
])
def test_read_method_errors(ws, method, message):
    result = read_method(ws, method)
    assert set(result) == {"error"} and message in result["error"]


def test_read_method_syntax_error(ws, work_calc):
    replace_in(work_calc, "return a + b", "return a +")
    assert "문법 오류" in read_method(ws, "calc.add")["error"]


# ---------------- get_callers ----------------

def test_get_callers_follows_module_and_from_imports(bug_ws):
    from sr_agent.tools.code import get_callers

    assert get_callers(bug_ws, "calc.payable") == ["calc.settle"]  # 같은 모듈 호출
    assert get_callers(bug_ws, "fees.fee") == ["calc.settle"]  # from order_settlement import fees → fees.fee()
    assert get_callers(bug_ws, "fees.rate") == ["calc.doubled"]  # from order_settlement.fees import rate
    assert get_callers(bug_ws, "calc.settle") == []
    assert "함수 없음" in get_callers(bug_ws, "calc.nothing")["error"]


def test_get_callers_real_target_s1_chain(tmp_path):
    from sr_agent.tools.code import get_callers
    from sr_agent.tools.workspace import prepare_workspace

    ws = prepare_workspace("SR-CALLERS", runs_dir=tmp_path)
    assert get_callers(ws, "discount.apply_coupon") == ["discount.apply_coupons"]
    assert get_callers(ws, "discount.apply_coupons") == ["discount.calc_final_amount"]
    assert "settlement.settle_order" in get_callers(ws, "discount.calc_final_amount")


# ---------------- apply_patch ----------------

GOOD = "def payable(amount, discount):\n    return max(0, amount - discount)\n"


def test_apply_patch_replaces_only_target(bug_ws):
    from sr_agent.tools.code import apply_patch, read_method

    calc = bug_ws.work / "src/order_settlement/calc.py"
    before = calc.read_text(encoding="utf-8")
    result = apply_patch(bug_ws, "calc.payable", GOOD)
    assert result["applied"] and result["compile_ok"] and result["error"] is None
    assert "+    return max(0, amount - discount)" in result["diff"]
    assert read_method(bug_ws, "calc.payable").endswith("return max(0, amount - discount)")
    after = calc.read_text(encoding="utf-8")
    assert before.split("def settle")[1] == after.split("def settle")[1]  # 다른 함수는 그대로


@pytest.mark.parametrize(("code", "message"), [
    ("def payable(amount):\n    return amount\n", "인자"),
    ("def payable(amount, disc):\n    return amount\n", "인자"),
    ("def payable(amount, discount=0):\n    return amount\n", "인자"),
    ("def other(amount, discount):\n    return 0\n", "이름"),
    ("x = 1\n", "함수 정의 하나"),
    (GOOD + "\n\ndef extra():\n    return 1\n", "함수 정의 하나"),
    ("def payable(amount, discount):\n    return (\n", "문법 오류"),
])
def test_apply_patch_rejects_and_keeps_file(bug_ws, code, message):
    from sr_agent.tools.code import apply_patch

    calc = bug_ws.work / "src/order_settlement/calc.py"
    before = calc.read_bytes()
    result = apply_patch(bug_ws, "calc.payable", code)
    assert not result["applied"] and message in result["error"]
    assert calc.read_bytes() == before


def test_apply_patch_allows_type_hints(bug_ws):
    from sr_agent.tools.code import apply_patch

    hinted = "def payable(amount: int, discount: int) -> int:\n    return max(0, amount - discount)\n"
    assert apply_patch(bug_ws, "calc.payable", hinted)["applied"]


def test_apply_patch_imports(bug_ws):
    from sr_agent.tools.code import apply_patch

    calc = bug_ws.work / "src/order_settlement/calc.py"
    result = apply_patch(bug_ws, "calc.payable", "def payable(amount, discount):\n    return math.floor(amount)\n",
                         {"add": ["import math"], "remove": []})
    assert result["applied"] and "import math" in calc.read_text(encoding="utf-8")
    # 다른 함수(doubled)가 아직 쓰는 import는 지울 수 없다
    still_used = {"add": [], "remove": ["from order_settlement.fees import rate"]}
    result = apply_patch(bug_ws, "calc.payable", GOOD, still_used)
    assert not result["applied"] and "아직 쓰는 import" in result["error"]
    # 더 이상 쓰지 않는 import는 지울 수 있다
    result = apply_patch(bug_ws, "calc.payable", GOOD, {"add": [], "remove": ["import math"]})
    assert result["applied"] and "import math" not in calc.read_text(encoding="utf-8")
    result = apply_patch(bug_ws, "calc.payable", GOOD, {"add": ["x = 1"], "remove": []})
    assert not result["applied"] and "import 문이 아님" in result["error"]


def test_apply_patch_class_method_keeps_indent(ws):
    from sr_agent.tools.code import apply_patch, read_method

    write(ws.work / "src/order_settlement/svc.py", "class Svc:\n    def run(self, x):\n        return x\n")
    result = apply_patch(ws, "svc.Svc.run", "def run(self, x):\n    return x + 1\n")
    assert result["applied"]
    assert read_method(ws, "svc.Svc.run").endswith("    def run(self, x):\n        return x + 1")
