"""판정 노드 (F5-4): 통과해야 하는 패치와 통과하면 안 되는 패치. 코드로만 판단하는지도 확인한다."""

import ast
from pathlib import Path

from sr_agent.tools.code import apply_patch
from sr_agent.tools.testing import write_repro_test
from sr_agent.workflow import judge as judge_mod
from sr_agent.workflow.judge import judge_defect, scope_violations
from tests.conftest import replace_in, write

REPRO = "from order_settlement import calc\n\n\ndef test_never_negative():\n    assert calc.payable(100, 300) == 0\n"
GOOD = "def payable(amount, discount):\n    \"\"\"결제 금액.\"\"\"\n    return max(0, amount - discount)\n"


def _checks(result):
    return {c.name: c.passed for c in result.checks}


def test_good_patch_passes(bug_ws):
    write_repro_test(bug_ws, "never_negative", REPRO)
    assert apply_patch(bug_ws, "calc.payable", GOOD)["applied"]
    result = judge_defect(bug_ws, "calc.payable", ["repro_tests/test_never_negative.py"])
    assert result.passed
    assert _checks(result) == {"repro_original_fails": True, "repro_patched_passes": True,
                               "existing_tests_pass": True, "change_scope": True, "reference_property": None}


def test_no_patch_fails(bug_ws):
    write_repro_test(bug_ws, "never_negative", REPRO)
    result = judge_defect(bug_ws, "calc.payable", ["repro_tests/test_never_negative.py"])
    assert not result.passed and _checks(result)["repro_patched_passes"] is False


def test_repro_that_does_not_reveal_bug_fails(bug_ws):
    write_repro_test(bug_ws, "weak", "from order_settlement import calc\n\n\ndef test_x():\n"
                                     "    assert calc.payable(500, 100) == 400\n")
    apply_patch(bug_ws, "calc.payable", GOOD)
    result = judge_defect(bug_ws, "calc.payable", ["repro_tests/test_weak.py"])
    assert not result.passed and _checks(result)["repro_original_fails"] is False


def test_patch_breaking_existing_tests_fails(bug_ws):
    write_repro_test(bug_ws, "never_negative", REPRO)
    apply_patch(bug_ws, "calc.payable", "def payable(amount, discount):\n    return 0\n")
    result = judge_defect(bug_ws, "calc.payable", ["repro_tests/test_never_negative.py"])
    assert not result.passed and _checks(result)["existing_tests_pass"] is False


def test_patch_touching_other_function_fails(bug_ws):
    write_repro_test(bug_ws, "never_negative", REPRO)
    apply_patch(bug_ws, "calc.payable", GOOD)
    replace_in(bug_ws.work / "src/order_settlement/calc.py", "return x * rate()", "return x * rate() + 0")
    result = judge_defect(bug_ws, "calc.payable", ["repro_tests/test_never_negative.py"])
    assert not result.passed and _checks(result)["change_scope"] is False
    assert "다른 함수 수정: calc.doubled" in result.checks[3].detail


def test_scope_violations(bug_ws):
    calc = bug_ws.work / "src/order_settlement/calc.py"
    assert scope_violations(bug_ws, "calc.payable") == []
    apply_patch(bug_ws, "calc.payable", GOOD, {"add": ["import math"], "remove": []})
    assert scope_violations(bug_ws, "calc.payable") == []  # 대상 함수 + import 변경은 허용
    replace_in(bug_ws.work / "src/order_settlement/fees.py", "// 100", "// 10")
    replace_in(bug_ws.work / "tests/test_calc.py", "== 8", "== 8 or True")
    write(bug_ws.work / "src/order_settlement/extra.py", "X = 1\n")
    calc.write_text(calc.read_text(encoding="utf-8") + "\n\ndef helper():\n    return 1\n", encoding="utf-8")
    problems = scope_violations(bug_ws, "calc.payable")
    assert "추가된 파일: src/order_settlement/extra.py" in problems
    assert "대상 밖 파일 수정: src/order_settlement/fees.py" in problems
    assert "대상 밖 파일 수정: tests/test_calc.py" in problems
    assert "함수 추가: calc.helper" in problems


def test_module_level_change_is_violation(bug_ws):
    replace_in(bug_ws.work / "src/order_settlement/calc.py", '"""정산 계산 (테스트용)."""', '"""바뀜."""')
    assert scope_violations(bug_ws, "calc.payable") == ["import 외 모듈 최상단 코드 수정"]


def test_judge_does_not_call_llm():
    """판정 노드에는 LLM 호출이 없다 (CLAUDE.md 규칙 4)."""
    tree = ast.parse(Path(judge_mod.__file__).read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not {m for m in imported if m and ("openai" in m or m.startswith("sr_agent.llm")
                                             or m.startswith("sr_agent.agents"))}
