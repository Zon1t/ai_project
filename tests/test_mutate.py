"""AST 뮤테이션 (F2-1): 연산자별 치환, 제외 대상, 최소 diff, 결함 버전 재현."""

import ast
import textwrap

import pytest

from sr_agent import config
from sr_agent.datagen.mutate import apply_mutant, list_mutants, mutate_source, read_source


def _mutants(source: str, operator: str | None = None):
    ms = mutate_source(textwrap.dedent(source), "mod", "src/order_settlement/mod.py")
    return [m for m in ms if operator is None or m.operator == operator]


def _applied(source: str, operator: str) -> set[str]:
    """해당 연산자 뮤턴트를 적용한 결과에서 바뀐 줄들."""
    text = textwrap.dedent(source)
    changed = set()
    for m in _mutants(source, operator):
        old_lines, new_lines = text.splitlines(), m.apply(text).splitlines()
        changed.update(n.strip() for o, n in zip(old_lines, new_lines, strict=False) if o != n)
    return changed


@pytest.mark.parametrize(("source", "operator", "expected"), [
    ("def f(a, b):\n    return a <= b\n", "ROR", {"return a < b"}),
    ("def f(a, b):\n    return a not in b\n", "ROR", {"return a in b"}),
    ("def f(a, b):\n    return a * b\n", "AOR", {"return a // b"}),
    ("def f(a):\n    a += 1\n    return a\n", "AOR", {"a -= 1"}),
    ("def f(a, b, c):\n    return a and b and c\n", "LCR", {"return a or b or c"}),
    ("def f(a):\n    return not a\n", "NEG", {"return a"}),
    ("def f(a, b):\n    if a and b:\n        return 1\n", "NEG", {"if not (a and b):"}),
    ("def f(a):\n    return a > 100\n", "CRP", {"return a > 101"}),
    ("def f():\n    return True\n", "CRP", {"return False"}),
    ("def f(a):\n    return max(0, a)\n", "MMR", {"return min(0, a)"}),
    ("def f(d):\n    d['k'] = 1\n    return d\n", "SDL", {"pass"}),
])
def test_operator_replaces_only_the_token(source, operator, expected):
    assert expected <= _applied(source, operator)


def test_or_and_chain_swaps_every_keyword():
    text = "def f(a, b, c):\n    return a or b or c\n"
    (m,) = _mutants(text, "LCR")
    assert m.apply(text) == "def f(a, b, c):\n    return a and b and c\n"


def test_korean_text_before_mutation_point():
    """ast 열 위치는 UTF-8 바이트 기준이라 한글이 앞에 있으면 문자 위치와 다르다."""
    text = 'def f(name, n):\n    if name == "홍길동" and n < 3:\n        return "한글"\n'
    lines = {m.apply(text).splitlines()[1].strip() for m in _mutants(text, "ROR")}
    assert lines == {'if name != "홍길동" and n < 3:', 'if name == "홍길동" and n <= 3:'}


def test_comments_and_crlf_preserved():
    text = "def f(a):\r\n    # 주석\r\n    return a + 1  # 끝\r\n"
    for m in _mutants(text):
        mutated = m.apply(text)
        assert mutated.count("\r\n") == 3
        assert "# 주석" in mutated and "# 끝" in mutated
        ast.parse(mutated)


def test_excluded_regions_are_not_mutated():
    text = '''
        LIMIT = 10 + 1


        @decorate(5)
        def f(a, b=3):
            """docstring 100"""
            msg = f"{a + 1}"
            print("x", 7)
            logger.info("y")
            return "%s" % a
        '''
    assert _mutants(text) == []


def test_sdl_keeps_first_assignment_but_deletes_reassignment():
    text = "def f(a):\n    x = a\n    x = x * 2\n    return x\n"
    deleted = [m.original for m in _mutants(text, "SDL")]
    assert deleted == ["x = x * 2"]


def test_string_formatting_is_not_arithmetic():
    assert _mutants('def f(a):\n    return "id=%s" % a + "!"\n', "AOR") == []


def test_methods_and_ids():
    text = "def f(a):\n    return a < 1\n\n\nclass C:\n    def g(self, b):\n        return b > 2\n"
    ms = _mutants(text)
    assert {m.method for m in ms} == {"mod.f", "mod.C.g"}
    assert len({m.mutant_id for m in ms}) == len(ms)
    assert [m.mutant_id for m in ms] == [m.mutant_id for m in _mutants(text)]  # 결정적


def test_target_project_mutants_are_valid():
    """실제 대상 프로젝트: 후보가 충분하고, 모두 파싱되며 원본과 다르다."""
    ms = list_mutants()
    assert len(ms) >= 300
    assert len({m.method for m in ms}) >= 70
    assert {m.operator for m in ms} == {"ROR", "AOR", "LCR", "NEG", "CRP", "MMR", "SDL"}
    texts = {}
    for m in ms:
        text = texts.setdefault(m.file, read_source(config.TARGET_ROOT / m.file))
        mutated = m.apply(text)
        assert mutated != text
        ast.parse(mutated)


def test_apply_mutant_to_copy_and_reject_changed_source(tmp_path):
    root = tmp_path / "proj"
    path = root / "src" / "order_settlement" / "mod.py"
    path.parent.mkdir(parents=True)
    text = "def f(a):\n    return a < 1\n"
    path.write_bytes(text.encode("utf-8"))
    (m,) = _mutants(text, "ROR")

    apply_mutant(root, m.to_dict())
    assert read_source(path) == "def f(a):\n    return a <= 1\n"
    with pytest.raises(ValueError, match="생성 당시와 다름"):
        apply_mutant(root, m)  # 이미 바뀐 파일에는 다시 적용하지 않는다
