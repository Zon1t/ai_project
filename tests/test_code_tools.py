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
