"""write_repro_test, get_coverage (PRD 7.3)."""

import pytest

from sr_agent import config
from sr_agent.tools.execution import run_tests
from sr_agent.tools.testing import get_coverage, write_repro_test

REPRO = "from order_settlement import calc\n\n\ndef test_never_negative():\n    assert calc.payable(100, 300) == 0\n"


def test_write_repro_test_and_run_on_original(bug_ws):
    result = write_repro_test(bug_ws, "never_negative", REPRO)
    assert result == {"written": True, "path": "repro_tests/test_never_negative.py"}
    assert (bug_ws.repro_tests / "test_never_negative.py").read_text(encoding="utf-8") == REPRO
    assert write_repro_test(bug_ws, "test_never_negative", REPRO)["path"] == result["path"]  # test_ 중복 안 붙음
    run = run_tests(bug_ws, "original", [result["path"]])
    assert run["failed"][0]["test"] == "repro_tests/test_never_negative.py::test_never_negative"
    assert not (bug_ws.work / "tests" / "test_never_negative.py").exists()  # 기존 테스트 폴더는 그대로


@pytest.mark.parametrize(("name", "code", "message"), [
    ("../evil", REPRO, "name"), ("Bad-Name", REPRO, "name"), ("", REPRO, "name"),
    ("ok", "def helper():\n    pass\n", "테스트 함수"), ("ok", "def test_x(:\n", "문법 오류"), ("ok", "  ", "비어"),
])
def test_write_repro_test_rejects(bug_ws, name, code, message):
    assert message in write_repro_test(bug_ws, name, code)["error"]


def test_get_coverage_finds_tests_and_caches(bug_ws, monkeypatch):
    result = get_coverage(bug_ws, "calc.payable")
    assert result == [{"test": "tests/test_calc.py::test_payable", "lines_hit": 1}]
    assert get_coverage(bug_ws, "calc.settle") == []  # 어떤 테스트도 실행하지 않음
    assert get_coverage(bug_ws, "fees.rate") == [{"test": "tests/test_calc.py::test_doubled", "lines_hit": 1}]
    monkeypatch.setattr("sr_agent.tools.testing._run", lambda *a, **k: pytest.fail("캐시를 써야 함"))
    assert get_coverage(bug_ws, "calc.payable")[0]["test"] == "tests/test_calc.py::test_payable"


def test_get_coverage_errors(bug_ws):
    assert "형식" in get_coverage(bug_ws, "payable")["error"]
    assert "함수 없음" in get_coverage(bug_ws, "calc.nothing")["error"]
    assert config.COVERAGE_TIMEOUT_S >= 30
