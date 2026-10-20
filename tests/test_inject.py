"""결함 버전 선별 (F2-1, F2-2): 채택 기준, 실패 정보 저장, 상한, 원본 불변."""

import json
import textwrap
from collections import Counter

import pytest

from sr_agent.datagen import inject as inject_mod
from sr_agent.datagen.inject import classify, inject, order_for_sampling
from sr_agent.datagen.llm_inject import function_spans, to_mutants
from sr_agent.datagen.mutate import list_mutants, read_source
from sr_agent.tools.workspace import hash_tree
from tests.conftest import write

LOOP_SOURCE = textwrap.dedent("""\
    def add(a, b):
        return a + b


    def countdown(n):
        while n > 0:
            n -= 1
        return n


    def untested(x):
        return x * 2
    """)

LOOP_TESTS = textwrap.dedent("""\
    from order_settlement import loop


    def test_add():
        assert loop.add(1, 2) == 3


    def test_countdown():
        assert loop.countdown(3) == 0
    """)


@pytest.fixture
def loop_target(tmp_path):
    root = tmp_path / "loop_target"
    write(root / "pyproject.toml", '[tool.pytest.ini_options]\npythonpath = ["src"]\ntestpaths = ["tests"]\n')
    write(root / "src" / "order_settlement" / "__init__.py", "")
    write(root / "src" / "order_settlement" / "loop.py", LOOP_SOURCE)
    write(root / "tests" / "test_loop.py", LOOP_TESTS)
    return root


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(("result", "expected"), [
    ({"passed": 2, "failed": []}, "survived"),
    ({"passed": 1, "failed": [{"test": "tests/test_a.py::test_x", "trace": "E"}]}, "killed"),
    ({"passed": 0, "failed": [{"test": "tests/test_a.py", "trace": "ImportError"}]}, "collect_error"),
    ({"error": "timeout: 테스트가 3초 안에 끝나지 않음"}, "timeout"),
    ({"error": "pytest 실행 실패"}, "error"),
])
def test_classify(result, expected):
    assert classify(result) == expected


def test_order_for_sampling_spreads_methods_and_is_deterministic(loop_target):
    ms = list_mutants(loop_target)
    ordered = order_for_sampling(ms, seed=1)
    assert sorted(m.mutant_id for m in ordered) == sorted(m.mutant_id for m in ms)
    n_methods = len({m.method for m in ms})
    assert len({m.method for m in ordered[:n_methods]}) == n_methods  # 첫 바퀴는 함수마다 하나
    assert ordered == order_for_sampling(ms, seed=1)
    assert ordered != order_for_sampling(ms, seed=2)


def test_inject_keeps_only_failing_mutants(loop_target, tmp_path):
    before = hash_tree(loop_target)
    out = tmp_path / "out"
    summary = inject(target_root=loop_target, out_dir=out, workers=2, timeout=3, llm_candidates=[])

    assert summary["target_unchanged"] and hash_tree(loop_target) == before
    assert summary["outcomes"]["killed"] == summary["accepted"] > 0
    assert summary["outcomes"]["survived"] >= 2  # untested의 뮤턴트
    assert summary["outcomes"]["timeout"] >= 1  # countdown의 무한 루프 (n += 1 등)
    assert summary["methods_uncovered"] == ["loop.untested"]

    records = _read_jsonl(out / "mutants.jsonl")
    assert len(records) == summary["accepted"]
    assert json.loads((out / "summary.json").read_text(encoding="utf-8")) == summary
    add = next(r for r in records if r["method"] == "loop.add" and r["operator"] == "AOR")
    assert add["inject_type"] == "ast"
    assert add["failed_tests"] == ["tests/test_loop.py::test_add"] and add["n_failed"] == 1
    assert "assert -1 == 3" in add["traces"][0]["trace"]
    assert "-    return a + b\r\n+    return a - b" in add["diff"]  # write()는 CRLF로 쓴다


def _llm_bug(root):
    """loop.add에 대한 LLM 주입 후보 하나."""
    text = read_source(root / "src" / "order_settlement" / "loop.py")
    span = next(s for s in function_spans(root) if s.method == "loop.add")
    (m,), _ = to_mutants(span, text, [{"bug_type": "계산", "description": "덧셈 대신 곱셈",
                                       "original": "return a + b", "mutated": "return a * b"}], set())
    return m


def test_inject_respects_caps_and_limit(loop_target, tmp_path, monkeypatch):
    killed = {"passed": 0, "failed": [{"test": "tests/test_loop.py::test_x", "trace": "E"}]}
    monkeypatch.setattr(inject_mod, "evaluate_mutant", lambda ws, m, text, timeout=None: killed)

    summary = inject(target_root=loop_target, out_dir=tmp_path / "a", caps={"ast": 1, "llm": 1},
                     llm_candidates=[_llm_bug(loop_target)], workers=2)
    records = _read_jsonl(tmp_path / "a" / "mutants.jsonl")
    assert len(records) == summary["accepted"] == 4  # AST 함수 3개 × 1 + LLM 1
    assert Counter((r["method"], r["inject_type"]) for r in records).most_common(1)[0][1] == 1
    assert summary["accepted_by_inject_type"] == {"ast": 3, "llm": 1}

    summary = inject(target_root=loop_target, out_dir=tmp_path / "b", limit=2, workers=2, llm_candidates=[])
    assert summary["accepted"] == 2 and summary["methods_uncovered"] is None
