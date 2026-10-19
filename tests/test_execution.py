import time

import pytest

from sr_agent import config
from sr_agent.tools.execution import run_diff, run_tests

from .conftest import replace_in, write

# run_tests ------------------------------------------------------------------


def test_run_tests_original_passes(ws):
    assert run_tests(ws, "original") == {"passed": 2, "failed": []}


def test_run_tests_reports_failure_only_in_patched(ws, work_calc):
    replace_in(work_calc, "a + b", "a - b")
    result = run_tests(ws, "patched")
    assert result["passed"] == 1
    assert [f["test"] for f in result["failed"]] == ["tests/test_calc.py::test_add"]
    assert "assert" in result["failed"][0]["trace"]
    assert run_tests(ws, "original") == {"passed": 2, "failed": []}


def test_run_tests_subset(ws):
    assert run_tests(ws, "original", ["tests/test_calc.py::test_add"]) == {"passed": 1, "failed": []}


@pytest.mark.parametrize("tests", [
    ["../original/tests/test_calc.py"],
    ["../../escape.py"],
    ["repro_tests/../../original/tests"],
])
def test_run_tests_rejects_outside_paths(ws, tests):
    assert "작업본 밖" in run_tests(ws, "patched", tests)["error"]


def test_run_tests_rejects_absolute_outside(ws, tmp_path):
    outside = write(tmp_path / "outside" / "test_evil.py", "def test_x():\n    pass\n")
    assert "error" in run_tests(ws, "patched", [str(outside)])


@pytest.mark.parametrize("version,tests", [("main", None), ("patched", []), ("patched", ["tests/test_none.py"])])
def test_run_tests_invalid_arguments(ws, version, tests):
    assert "error" in run_tests(ws, version, tests)


def test_repro_test_fails_on_original_and_passes_on_patched(ws, work_calc):
    write(ws.repro_tests / "test_repro.py",
          "from order_settlement import calc\n\n\ndef test_divide_by_zero():\n    assert calc.divide(1, 0) is None\n")
    original = run_tests(ws, "original")
    assert original["passed"] == 2
    assert [f["test"] for f in original["failed"]] == ["repro_tests/test_repro.py::test_divide_by_zero"]
    assert "ZeroDivisionError" in original["failed"][0]["trace"]

    replace_in(work_calc, "    return a / b", "    if b == 0:\n        return None\n    return a / b")
    assert run_tests(ws, "patched") == {"passed": 3, "failed": []}
    assert run_tests(ws, "patched", ["repro_tests/test_repro.py"]) == {"passed": 1, "failed": []}


def test_run_tests_timeout(ws):
    write(ws.work / "tests" / "test_spin.py",
          "from order_settlement import calc\n\n\ndef test_spin():\n    calc.spin()\n")
    start = time.monotonic()
    result = run_tests(ws, "patched", ["tests/test_spin.py"], timeout=3)
    assert "timeout" in result["error"]
    assert time.monotonic() - start < 8


def test_run_tests_default_timeout_is_config(monkeypatch, ws):
    seen = {}

    def fake_run(cmd, cwd, timeout, stdin=None, extra_env=None):
        seen["timeout"] = timeout

    monkeypatch.setattr("sr_agent.tools.execution._run", fake_run)
    run_tests(ws, "original")
    assert seen["timeout"] == config.SUBPROCESS_TIMEOUT_S == 10


def test_collection_error_reported(ws, work_calc):
    write(work_calc, "def add(a, b)\n    return a + b\n")
    result = run_tests(ws, "patched")
    assert result["passed"] == 0
    assert any("SyntaxError" in f["trace"] for f in result["failed"])


def test_trace_is_trimmed(ws):
    write(ws.work / "tests" / "test_long.py", "def test_long():\n    assert 'x' * 20000 == ''\n")
    trace = run_tests(ws, "patched", ["tests/test_long.py"])["failed"][0]["trace"]
    assert len(trace) <= config.MAX_TRACE_CHARS + 50


def test_failures_are_capped(ws):
    write(ws.work / "tests" / "test_many.py",
          "import pytest\n\n\n@pytest.mark.parametrize('i', range(15))\ndef test_fail(i):\n    assert False\n")
    result = run_tests(ws, "patched", ["tests/test_many.py"])
    assert len(result["failed"]) == config.MAX_FAILURES_REPORTED
    assert result["omitted"] == 15 - config.MAX_FAILURES_REPORTED


def test_secrets_not_passed_to_child(ws, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-dummy")
    write(ws.work / "tests" / "test_env.py",
          "import os\n\n\ndef test_no_secrets():\n"
          "    assert not [k for k in os.environ if 'API_KEY' in k]\n")
    assert run_tests(ws, "patched", ["tests/test_env.py"]) == {"passed": 1, "failed": []}


def test_run_tests_leaves_original_untouched(ws, mini_target):
    from sr_agent.tools.workspace import verify_original_intact

    run_tests(ws, "original")
    assert verify_original_intact(ws, source=mini_target)["unchanged"] is True
    assert not list(ws.original.rglob("__pycache__"))


# run_diff -------------------------------------------------------------------


def test_run_diff_same(ws):
    result = run_diff(ws, "calc.add", [[1, 2], {"args": [3], "kwargs": {"b": 4}}])
    assert [r["same"] for r in result] == [True, True]
    assert result[0]["original"] == {"ok": True, "value": 3}
    assert result[1]["patched"] == {"ok": True, "value": 7}


def test_run_diff_detects_difference(ws, work_calc):
    replace_in(work_calc, "a + b", "a - b")
    result = run_diff(ws, "calc.add", [[1, 2], [0, 0]])
    assert result[0] == {"input": [1, 2], "original": {"ok": True, "value": 3},
                         "patched": {"ok": True, "value": -1}, "same": False}
    assert result[1]["same"] is True


def test_run_diff_captures_exceptions(ws):
    result = run_diff(ws, "calc.divide", [[1, 0]])
    assert result[0]["original"] == {"ok": False, "exception": "ZeroDivisionError: division by zero"}
    assert result[0]["same"] is True


def test_run_diff_date_values_and_package_prefix(ws):
    result = run_diff(ws, "order_settlement.calc.next_day", [[{"$date": "2024-02-28"}]])
    assert result[0]["original"] == {"ok": True, "value": {"$date": "2024-02-29"}}


def test_run_diff_ignores_stdout_noise(ws):
    assert run_diff(ws, "calc.shout", [["hi"]])[0]["patched"] == {"ok": True, "value": "HI"}


@pytest.mark.parametrize("method", ["calc", "", "calc.add; import os", "../calc.add", "calc.add()", 123])
def test_run_diff_rejects_bad_method(ws, method):
    assert "error" in run_diff(ws, method, [[1, 2]])


def test_run_diff_missing_function(ws):
    assert "메서드를 찾을 수 없음" in run_diff(ws, "calc.nope", [[1]])["error"]


@pytest.mark.parametrize("inputs", [
    [], "1,2", [1, 2], [{"x": 1}], [{"args": 1}], [[object()]], [[1, 2]] * (config.MAX_DIFF_INPUTS + 1),
])
def test_run_diff_rejects_bad_inputs(ws, inputs):
    assert "error" in run_diff(ws, "calc.add", inputs)


def test_run_diff_timeout(ws):
    start = time.monotonic()
    assert "timeout" in run_diff(ws, "calc.spin", [[]], timeout=2)["error"]
    assert time.monotonic() - start < 8


def test_run_diff_patched_import_error(ws, work_calc):
    write(work_calc, "def add(a, b)\n")
    assert run_diff(ws, "calc.add", [[1, 2]])["error"].startswith("patched: 모듈 import 실패")


def test_run_diff_long_value_is_shortened(ws):
    result = run_diff(ws, "calc.shout", [["a" * 2000]])[0]
    assert result["same"] is True
    assert isinstance(result["original"], str) and result["original"].endswith("...(생략)")
