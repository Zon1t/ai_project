"""공통 픽스처: 빠르게 도는 작은 가짜 대상 프로젝트와 그 작업 디렉터리."""

import textwrap
from pathlib import Path

import pytest

from sr_agent.tools.workspace import prepare_workspace

CALC_SOURCE = textwrap.dedent("""\
    from datetime import timedelta


    def add(a, b):
        return a + b


    def divide(a, b):
        return a / b


    def next_day(d):
        return d + timedelta(days=1)


    def shout(text):
        print("noise")
        return text.upper()


    def spin():
        while True:
            pass
    """)

TEST_SOURCE = textwrap.dedent("""\
    from order_settlement import calc


    def test_add():
        assert calc.add(1, 2) == 3


    def test_divide():
        assert calc.divide(6, 3) == 2
    """)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {path}"
    path.write_text(text.replace(old, new), encoding="utf-8")


@pytest.fixture
def mini_target(tmp_path) -> Path:
    root = tmp_path / "mini_target"
    write(root / "pyproject.toml", '[tool.pytest.ini_options]\npythonpath = ["src"]\ntestpaths = ["tests"]\n')
    write(root / "src" / "order_settlement" / "__init__.py", "")
    write(root / "src" / "order_settlement" / "calc.py", CALC_SOURCE)
    write(root / "tests" / "test_calc.py", TEST_SOURCE)
    return root


@pytest.fixture
def ws(mini_target, tmp_path):
    return prepare_workspace("SR-0001", source=mini_target, runs_dir=tmp_path / "runs")


@pytest.fixture
def work_calc(ws) -> Path:
    return ws.work / "src" / "order_settlement" / "calc.py"
