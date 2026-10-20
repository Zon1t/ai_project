"""AST 뮤테이션으로 결함 후보(뮤턴트)를 만든다 (PRD F2-1).

원본 파일에서 바뀌는 토큰만 갈아 끼워 diff를 최소로 한다 (주석·포맷 유지).
바꾼 소스를 다시 파싱해 의도한 AST 노드 하나만 바뀌었는지 검사하고, 어긋나면 후보에서 뺀다.
대상은 함수 본문뿐이다. docstring, f-string 내부, print·로그 호출, 기본 인자값, 데코레이터,
모듈 최상단 코드는 바꾸지 않는다.
"""

import ast
import copy
import difflib
import hashlib
import re
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from sr_agent import config

OPERATORS = {
    "ROR": "비교 연산자 교체 (경계값)",
    "AOR": "산술 연산자 교체",
    "LCR": "논리 연산자 교체 (and/or)",
    "NEG": "조건 부정·not 제거",
    "CRP": "숫자·불리언 상수 교체",
    "MMR": "max/min 교체 (상·하한)",
    "SDL": "문장 삭제 (누락)",
}

_CMP_SWAP = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}
_CMP_TOKEN = {
    ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=",
    ast.In: "in", ast.NotIn: "not in", ast.Is: "is", ast.IsNot: "is not",
}
_CMP_PATTERN = {
    ast.Lt: r"<", ast.LtE: r"<=", ast.Gt: r">", ast.GtE: r">=", ast.Eq: r"==", ast.NotEq: r"!=",
    ast.In: r"\bin\b", ast.NotIn: r"\bnot\s+in\b", ast.Is: r"\bis\b", ast.IsNot: r"\bis\s+not\b",
}
_ARITH_SWAP = {
    ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv, ast.FloorDiv: ast.Mult,
    ast.Div: ast.Mult, ast.Mod: ast.FloorDiv,
}
_ARITH_TOKEN = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.FloorDiv: "//", ast.Div: "/", ast.Mod: "%"}
_BOOL_SWAP = {ast.And: (ast.Or, "and", "or"), ast.Or: (ast.And, "or", "and")}
_MINMAX = {"max": "min", "min": "max"}
_LOG_NAMES = {"logger", "log", "logging"}
_SIMPLE_TEST = (ast.Name, ast.Attribute, ast.Call, ast.Subscript, ast.Compare, ast.Constant)

_Parents = dict[int, tuple[ast.AST, str, int | None]]
_TreeEdit = Callable[[ast.AST, _Parents], None]


@dataclass(frozen=True)
class Mutant:
    """결함 후보 하나. start·end는 원본 파일 텍스트 기준 문자 오프셋."""

    mutant_id: str
    operator: str
    description: str
    method: str  # "모듈.함수"
    file: str  # 대상 프로젝트 루트 기준 posix 경로
    file_sha256: str  # 원본 파일 해시. 다르면 대상 코드가 바뀐 것이므로 적용 거부
    lineno: int
    start: int
    end: int
    original: str
    replacement: str

    def apply(self, text: str) -> str:
        """원본 텍스트에 이 뮤테이션을 적용한 텍스트."""
        if text[self.start:self.end] != self.original:
            raise ValueError(f"{self.mutant_id}: 원본 텍스트가 기록과 다름")
        return text[:self.start] + self.replacement + text[self.end:]

    def diff(self, text: str) -> str:
        """원본 대비 unified diff (앞뒤 1줄)."""
        lines = difflib.unified_diff(
            text.splitlines(keepends=True), self.apply(text).splitlines(keepends=True),
            fromfile=f"a/{self.file}", tofile=f"b/{self.file}", n=1,
        )
        return "".join(lines)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _Candidate:
    operator: str
    description: str
    node_index: int
    start: int
    end: int
    replacement: str
    tree_edit: _TreeEdit


class _Source:
    """ast 위치(줄, UTF-8 바이트 열)를 텍스트 문자 오프셋으로 바꾼다."""

    def __init__(self, text: str):
        self.text = text
        self._lines = text.split("\n")
        self._starts = [0]
        for line in self._lines:
            self._starts.append(self._starts[-1] + len(line) + 1)

    def offset(self, lineno: int, col_bytes: int) -> int:
        line = self._lines[lineno - 1]
        return self._starts[lineno - 1] + len(line.encode("utf-8")[:col_bytes].decode("utf-8"))

    def start(self, node: ast.AST) -> int:
        return self.offset(node.lineno, node.col_offset)

    def end(self, node: ast.AST) -> int:
        return self.offset(node.end_lineno, node.end_col_offset)

    def segment(self, node: ast.AST) -> str:
        return self.text[self.start(node):self.end(node)]


def _parents(nodes: list[ast.AST]) -> _Parents:
    parents: _Parents = {}
    for node in nodes:
        for field, value in ast.iter_fields(node):
            if isinstance(value, list):
                for i, child in enumerate(value):
                    if isinstance(child, ast.AST):
                        parents[id(child)] = (node, field, i)
            elif isinstance(value, ast.AST):
                parents[id(value)] = (node, field, None)
    return parents


def _replace_node(parents: _Parents, node: ast.AST, new: ast.AST) -> None:
    parent, field, index = parents[id(node)]
    if index is None:
        setattr(parent, field, new)
    else:
        getattr(parent, field)[index] = new


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _body_nodes(func: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[ast.AST]:
    """함수 본문의 노드. docstring, f-string 내부, 중첩 함수의 기본값·데코레이터·주석은 건너뛴다."""
    body = func.body[1:] if func.body and _is_docstring(func.body[0]) else func.body
    stack = list(reversed(body))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Call) and _is_log_call(node)):
            continue  # f-string 내부, print·로그 인자는 바꿔도 테스트가 잡지 못한다
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            children = node.body
        elif isinstance(node, ast.Lambda):
            children = [node.body]
        elif isinstance(node, ast.AnnAssign):
            children = [node.target] + ([node.value] if node.value else [])
        else:
            children = list(ast.iter_child_nodes(node))
        stack.extend(reversed(children))


def _functions(tree: ast.Module, module: str) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield f"{module}.{node.name}", node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    yield f"{module}.{node.name}.{item.name}", item


def _is_stringish(node: ast.AST) -> bool:
    return isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and isinstance(node.value, str | bytes))


def _gap_token(src: _Source, gap_start: int, gap_end: int, pattern: str) -> tuple[int, int] | None:
    """두 피연산자 사이 텍스트에서 연산자 토큰 위치를 찾는다."""
    m = re.search(pattern, src.text[gap_start:gap_end])
    return (gap_start + m.start(), gap_start + m.end()) if m else None


def _set_attr(attr: str, value_factory: Callable[[], object]) -> _TreeEdit:
    def edit(node: ast.AST, parents: _Parents) -> None:
        setattr(node, attr, value_factory())
    return edit


def _cmp_candidates(node: ast.Compare, idx: int, src: _Source) -> Iterator[_Candidate]:
    if len(node.ops) != 1 or type(node.ops[0]) not in _CMP_SWAP:
        return
    old = type(node.ops[0])
    new = _CMP_SWAP[old]
    span = _gap_token(src, src.end(node.left), src.start(node.comparators[0]), _CMP_PATTERN[old])
    if span:
        def edit(n: ast.AST, parents: _Parents) -> None:
            n.ops[0] = new()
        yield _Candidate("ROR", f"`{_CMP_TOKEN[old]}` → `{_CMP_TOKEN[new]}`", idx, *span, _CMP_TOKEN[new], edit)


def _arith_candidates(node: ast.BinOp | ast.AugAssign, idx: int, src: _Source) -> Iterator[_Candidate]:
    old = type(node.op)
    if old not in _ARITH_SWAP:
        return
    new = _ARITH_SWAP[old]
    if isinstance(node, ast.BinOp):
        if _is_stringish(node.left) or _is_stringish(node.right):
            return
        left, right, suffix = node.left, node.right, ""
    else:
        if _is_stringish(node.value):
            return
        left, right, suffix = node.target, node.value, "="
    old_tok, new_tok = _ARITH_TOKEN[old] + suffix, _ARITH_TOKEN[new] + suffix
    span = _gap_token(src, src.end(left), src.start(right), re.escape(old_tok))
    if span:
        yield _Candidate("AOR", f"`{old_tok}` → `{new_tok}`", idx, *span, new_tok, _set_attr("op", new))


def _bool_candidates(node: ast.BoolOp, idx: int, src: _Source) -> Iterator[_Candidate]:
    new, old_kw, new_kw = _BOOL_SWAP[type(node.op)]
    start, end = src.start(node), src.end(node)
    pieces = [src.text[start:src.end(node.values[0])]]
    for prev, value in zip(node.values, node.values[1:], strict=False):
        gap = src.text[src.end(prev):src.start(value)]
        pieces.append(re.sub(rf"\b{old_kw}\b", new_kw, gap, count=1))
        pieces.append(src.segment(value))
    pieces.append(src.text[src.end(node.values[-1]):end])
    yield _Candidate("LCR", f"`{old_kw}` → `{new_kw}`", idx, start, end, "".join(pieces), _set_attr("op", new))


def _not_remove_candidates(node: ast.UnaryOp, idx: int, src: _Source) -> Iterator[_Candidate]:
    start = src.start(node)
    m = re.match(r"not\s*", src.text[start:src.start(node.operand)])
    if m:
        def edit(n: ast.AST, parents: _Parents) -> None:
            _replace_node(parents, n, n.operand)
        yield _Candidate("NEG", "`not` 제거", idx, start, start + m.end(), "", edit)


def _negate_test_candidates(node: ast.If | ast.IfExp, idx: int, src: _Source) -> Iterator[_Candidate]:
    test = node.test
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return  # not 제거 쪽에서 다룬다
    seg = src.segment(test)
    replacement = f"not {seg}" if isinstance(test, _SIMPLE_TEST) else f"not ({seg})"

    def edit(n: ast.AST, parents: _Parents) -> None:
        n.test = ast.UnaryOp(op=ast.Not(), operand=n.test)
    yield _Candidate("NEG", "조건 부정", idx, src.start(test), src.end(test), replacement, edit)


def _const_candidates(node: ast.Constant, idx: int, src: _Source) -> Iterator[_Candidate]:
    value = node.value
    if isinstance(value, bool):
        new = not value
    elif isinstance(value, int | float):
        new = {0: 1, 1: 0}.get(value, value + 1)
        new = type(value)(new)
    else:
        return
    yield _Candidate("CRP", f"`{value!r}` → `{new!r}`", idx, src.start(node), src.end(node), repr(new),
                     _set_attr("value", lambda: new))


def _minmax_candidates(node: ast.Call, idx: int, src: _Source) -> Iterator[_Candidate]:
    if isinstance(node.func, ast.Name) and node.func.id in _MINMAX:
        old, new = node.func.id, _MINMAX[node.func.id]

        def edit(n: ast.AST, parents: _Parents) -> None:
            n.func.id = new
        yield _Candidate("MMR", f"`{old}` → `{new}`", idx, src.start(node.func), src.end(node.func), new, edit)


def _is_log_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "print"
    return isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in _LOG_NAMES


def _deletable(stmt: ast.stmt, assigned_before: Callable[[str, int], bool]) -> bool:
    """지워도 NameError 없이 동작이 바뀌는 문장인지. 로그 호출은 테스트가 잡지 못하므로 뺀다."""
    if isinstance(stmt, ast.AugAssign):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return not _is_log_call(stmt.value)
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target = stmt.targets[0]
        if isinstance(target, ast.Subscript | ast.Attribute):
            return True
        if isinstance(target, ast.Name):
            return assigned_before(target.id, stmt.lineno)
    return False


def _stmt_delete_candidates(node: ast.stmt, idx: int, src: _Source,
                            assigned_before: Callable[[str, int], bool]) -> Iterator[_Candidate]:
    if _deletable(node, assigned_before):
        def edit(n: ast.AST, parents: _Parents) -> None:
            _replace_node(parents, n, ast.Pass())
        first_line = src.segment(node).split("\n")[0].strip()
        yield _Candidate("SDL", f"`{first_line}` 삭제", idx, src.start(node), src.end(node), "pass", edit)


def _assigned_before_fn(func: ast.FunctionDef | ast.AsyncFunctionDef) -> Callable[[str, int], bool]:
    params = {a.arg for a in ast.walk(func.args) if isinstance(a, ast.arg)}
    stores = [(n.lineno, n.id) for n in ast.walk(func) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]

    def assigned_before(name: str, lineno: int) -> bool:
        return name in params or any(line < lineno and n == name for line, n in stores)
    return assigned_before


def _node_candidates(node: ast.AST, idx: int, src: _Source,
                     assigned_before: Callable[[str, int], bool]) -> Iterator[_Candidate]:
    if isinstance(node, ast.Compare):
        yield from _cmp_candidates(node, idx, src)
    elif isinstance(node, ast.BinOp | ast.AugAssign):
        yield from _arith_candidates(node, idx, src)
    elif isinstance(node, ast.BoolOp):
        yield from _bool_candidates(node, idx, src)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        yield from _not_remove_candidates(node, idx, src)
    elif isinstance(node, ast.IfExp):
        yield from _negate_test_candidates(node, idx, src)
    elif isinstance(node, ast.Constant):
        yield from _const_candidates(node, idx, src)
    elif isinstance(node, ast.Call):
        yield from _minmax_candidates(node, idx, src)
    if isinstance(node, ast.If):
        yield from _negate_test_candidates(node, idx, src)
    if isinstance(node, ast.stmt):
        yield from _stmt_delete_candidates(node, idx, src, assigned_before)


def _verified(tree: ast.Module, cand: _Candidate, mutated: str) -> bool:
    """바꾼 소스를 파싱한 AST가 '원본 AST에 의도한 수정 하나만 한 것'과 같은지."""
    expected = copy.deepcopy(tree)
    nodes = list(ast.walk(expected))
    cand.tree_edit(nodes[cand.node_index], _parents(nodes))
    try:
        got = ast.parse(mutated)
    except SyntaxError:
        return False
    return ast.dump(got) == ast.dump(expected)


def _mutant_id(file: str, start: int, end: int, replacement: str) -> str:
    digest = hashlib.sha1(f"{file}:{start}:{end}:{replacement}".encode()).hexdigest()
    return "MUT-" + digest[:8]


def mutate_source(text: str, module: str, file: str) -> list[Mutant]:
    """모듈 소스 하나에서 검증을 통과한 뮤턴트를 모두 만든다 (위치 순)."""
    tree = ast.parse(text)
    src = _Source(text)
    file_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    index = {id(n): i for i, n in enumerate(ast.walk(tree))}
    mutants: dict[str, Mutant] = {}
    for method, func in _functions(tree, module):
        assigned_before = _assigned_before_fn(func)
        for node in _body_nodes(func):
            for cand in _node_candidates(node, index[id(node)], src, assigned_before):
                mutated = text[:cand.start] + cand.replacement + text[cand.end:]
                if not _verified(tree, cand, mutated):
                    continue
                mid = _mutant_id(file, cand.start, cand.end, cand.replacement)
                mutants.setdefault(mid, Mutant(
                    mutant_id=mid, operator=cand.operator, description=cand.description, method=method,
                    file=file, file_sha256=file_sha, lineno=text.count("\n", 0, cand.start) + 1,
                    start=cand.start, end=cand.end, original=text[cand.start:cand.end],
                    replacement=cand.replacement,
                ))
    return sorted(mutants.values(), key=lambda m: m.start)


def package_files(target_root: Path | None = None) -> list[Path]:
    """대상 패키지의 모듈 파일 목록."""
    root = Path(target_root or config.TARGET_ROOT)
    return sorted((root / config.TARGET_SRC_SUBDIR / config.TARGET_PACKAGE).glob("*.py"))


def read_source(path: Path) -> str:
    """줄바꿈을 바꾸지 않고 그대로 읽는다 (쓸 때도 바이트 그대로 쓴다)."""
    return path.read_bytes().decode("utf-8")


def list_mutants(target_root: Path | None = None) -> list[Mutant]:
    """대상 프로젝트 전체의 뮤턴트 후보 (파일·위치 순)."""
    root = Path(target_root or config.TARGET_ROOT)
    mutants = []
    for path in package_files(root):
        rel = path.relative_to(root).as_posix()
        mutants.extend(mutate_source(read_source(path), path.stem, rel))
    return mutants


def apply_mutant(project_root: Path, mutant: Mutant | dict) -> Path:
    """대상 프로젝트 복사본(project_root)에 결함을 심는다. 원본 파일 해시가 기록과 다르면 거부."""
    m = mutant if isinstance(mutant, Mutant) else Mutant(**{k: mutant[k] for k in Mutant.__dataclass_fields__})
    path = Path(project_root) / m.file
    text = read_source(path)
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != m.file_sha256:
        raise ValueError(f"{m.mutant_id}: {m.file} 내용이 생성 당시와 다름 (대상 코드 변경 시 결함 데이터 재생성 필요)")
    path.write_bytes(m.apply(text).encode("utf-8"))
    return path
