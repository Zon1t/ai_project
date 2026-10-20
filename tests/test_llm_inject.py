"""LLM 결함 주입 (F2-1 ②): 제안 검증, 중복 제거, 저장 응답에서 후보 재구성. LLM은 가짜로 대체한다."""

import json
import textwrap

import pytest

from sr_agent import llm
from sr_agent.datagen import llm_inject
from sr_agent.datagen.llm_inject import InjectedBug, InjectedBugs, function_spans, load_candidates, to_mutants
from sr_agent.datagen.mutate import read_source
from tests.conftest import write

SOURCE = textwrap.dedent('''\
    """할인 계산."""


    def capped(amount, rate):
        """할인액 (상한 1만 원)."""
        discount = amount * rate // 100
        if discount > 10000:
            discount = 10000
        return discount


    def other(x):
        return x
    ''')


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "proj"
    path = root / "src" / "order_settlement" / "disc.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(SOURCE.encode("utf-8"))
    return root


def _bug(original, mutated, bug_type="경계 처리 누락"):
    return {"bug_type": bug_type, "description": "상한 적용 누락", "original": original, "mutated": mutated}


def _span(root, method="disc.capped"):
    return next(s for s in function_spans(root) if s.method == method)


def test_function_spans(root):
    span = _span(root)
    assert span.module_doc == "할인 계산." and span.file == "src/order_settlement/disc.py"
    assert span.source.startswith("def capped") and span.source.endswith("return discount")


def test_to_mutants_validates_and_builds_mutant(root):
    text = read_source(root / "src/order_settlement/disc.py")
    bugs = [
        _bug("    if discount > 10000:\n        discount = 10000\n", ""),  # 상한 블록 삭제
        _bug("return x", "return x + 1"),  # 다른 함수의 코드
        _bug("discount", "d"),  # 여러 번 나옴
        _bug("discount = amount * rate // 100", "discount = amount * rate // 100 +"),  # 문법 오류
        _bug("discount = amount * rate // 100", "discount = (amount * rate) // 100"),  # 동작 같음
    ]
    mutants, rejects = to_mutants(_span(root), text, bugs, set())
    assert rejects == {"original_not_unique": 2, "syntax_error": 1, "no_change": 1}
    (m,) = mutants
    assert m.inject_type == "llm" and m.operator == "LLM" and m.method == "disc.capped"
    assert m.description == "[경계 처리 누락] 상한 적용 누락"
    assert "if discount > 10000" not in m.apply(text)


def test_to_mutants_drops_duplicates(root):
    text = read_source(root / "src/order_settlement/disc.py")
    bug = _bug("if discount > 10000:", "if discount >= 10000:")
    known: set[str] = set()
    first, _ = to_mutants(_span(root), text, [bug], known)
    again, rejects = to_mutants(_span(root), text, [bug], known)
    assert len(first) == 1 and again == [] and rejects == {"duplicate": 1}


def test_load_candidates_skips_ast_duplicates(root, tmp_path):
    raw = tmp_path / "raw.jsonl"
    rows = [
        {"method": "disc.capped", "status": "ok", "bugs": [
            _bug("if discount > 10000:", "if discount >= 10000:"),  # AST(ROR)와 같은 결과 → 중복
            _bug("    if discount > 10000:\n        discount = 10000\n", ""),
        ]},
        {"method": "disc.other", "status": "error", "bugs": []},
    ]
    write(raw, "\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    candidates, rejects = load_candidates(root, raw)
    assert len(candidates) == 1 and rejects == {"duplicate": 1}


def test_request_bugs_and_generate_resume(root, tmp_path, monkeypatch):
    calls = []

    def fake(client, **kwargs):
        calls.append(kwargs)
        bugs = InjectedBugs(bugs=[InjectedBug(**_bug("return x", "return -x"))])
        return llm.ParsedResponse(bugs, {"input_tokens": 100, "output_tokens": 50}, 0.1, "r")

    monkeypatch.setattr(llm, "parse_structured", fake)
    monkeypatch.setattr(llm_inject, "function_spans", lambda: function_spans(root))
    raw = tmp_path / "raw.jsonl"
    summary = llm_inject.generate(client=object(), raw_path=raw, workers=1, runs_dir=tmp_path)
    assert summary["requested"] == 2 and summary["status"] == {"ok": 2}
    assert "만들 결함 수" in calls[0]["input"] and "[함수 소스]" in calls[0]["input"]
    assert llm_inject.generate(client=object(), raw_path=raw, workers=1, runs_dir=tmp_path)["requested"] == 0


def test_instructions_are_fixed_text():
    assert "{" not in llm_inject.INSTRUCTIONS
