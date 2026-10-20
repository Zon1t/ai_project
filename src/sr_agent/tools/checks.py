"""정적 검사 도구 check_deprecated, check_style, check_security (PRD 7.3). 수정 에이전트와 판정 노드가 쓴다.

모두 AST 기반이고, check_security는 bandit 결과를 함께 쓴다.
결과의 rule_id는 사내 규칙 KB(data/rules/rules.json)의 ID다.
version: "original"(원본 복사본) | "patched"(작업본). method를 주면 그 함수 범위만 본다.
반환: [{"location": "파일:줄", "rule_id", "detail"}] 또는 {"error"}
"""

import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path

from sr_agent import config
from sr_agent.tools.code import _split_method, find_function
from sr_agent.tools.execution import _run
from sr_agent.tools.workspace import Workspace, WorkspaceError

_TYPING_ALIASES = {"Dict": "DEP-TYP-01", "List": "DEP-TYP-01", "Tuple": "DEP-TYP-01", "Set": "DEP-TYP-01",
                   "Optional": "DEP-TYP-02", "Union": "DEP-TYP-02"}
_SQL = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
_PII = re.compile(r"\b(name|email|phone)\b")
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
_BANDIT_RULES = {"B608": "SEC-SQL-01", "B303": "SEC-CRY-01", "B324": "SEC-CRY-01", "B311": "SEC-CRY-02",
                 "B307": "SEC-INP-01", "B102": "SEC-INP-01", "B301": "SEC-INP-01", "B403": "SEC-INP-01"}


def _files(ws: Workspace, version: str, method: str | None) -> tuple[list[tuple[Path, str]], tuple[int, int] | None]:
    """검사할 (파일, 상대 경로) 목록과 함수 줄 범위. method가 있으면 그 함수의 파일만."""
    root = ws.version_dir(version)
    pkg = root / config.TARGET_SRC_SUBDIR / config.TARGET_PACKAGE
    if method is None:
        return [(p, p.relative_to(root).as_posix()) for p in sorted(pkg.glob("*.py"))], None
    parsed = _split_method(method)
    if parsed is None:
        raise WorkspaceError(f"method 형식 오류 (모듈.함수): {method!r}")
    module, names = parsed
    path = pkg / f"{module}.py"
    if not path.is_file():
        raise WorkspaceError(f"모듈 없음: {module}")
    node = find_function(ast.parse(path.read_bytes().decode("utf-8")), names)
    if node is None:
        raise WorkspaceError(f"함수 없음: {method}")
    start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
    return [(path, path.relative_to(root).as_posix())], (start, node.end_lineno)


def _run_check(ws: Workspace, version: str, method: str | None, finder) -> list[dict] | dict:
    try:
        files, span = _files(ws, version, method)
    except WorkspaceError as e:
        return {"error": str(e)}
    out = []
    for path, rel in files:
        text = path.read_bytes().decode("utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            return {"error": f"{rel} 문법 오류: {e}"}
        for line, rule_id, detail in finder(tree, text, path):
            if span is None or span[0] <= line <= span[1]:
                out.append({"location": f"{rel}:{line}", "rule_id": rule_id, "detail": detail})
    return sorted(out, key=lambda f: (f["location"].split(":")[0], int(f["location"].split(":")[1]), f["rule_id"]))


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        base = f.value.id if isinstance(f.value, ast.Name) else (f.value.attr if isinstance(f.value, ast.Attribute)
                                                                 else "")
        return f"{base}.{f.attr}"
    return f.id if isinstance(f, ast.Name) else ""


# ---------------- 지원 종료 API ----------------

def _deprecated(tree: ast.Module, text: str, path: Path):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name.endswith(".utcnow"):
                yield node.lineno, "DEP-DT-01", "datetime.utcnow()"
            elif name.endswith(".utcfromtimestamp"):
                yield node.lineno, "DEP-DT-02", "datetime.utcfromtimestamp()"
            elif name.endswith((".localize", ".normalize")):
                yield node.lineno, "DEP-TZ-02", f"pytz {name.rsplit('.', 1)[1]}()"
            if any(k.arg == "detect_types" for k in node.keywords):
                yield node.lineno, "DEP-SQL-01", "sqlite3 detect_types (기본 datetime 어댑터)"
        elif isinstance(node, ast.Import) and any(a.name == "pytz" for a in node.names):
            yield node.lineno, "DEP-TZ-01", "import pytz"
        elif isinstance(node, ast.ImportFrom) and node.module == "pytz":
            yield node.lineno, "DEP-TZ-01", "from pytz import"
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "pytz":
            yield node.lineno, "DEP-TZ-01", f"pytz.{node.attr}"
        elif isinstance(node, ast.Name) and node.id in _TYPING_ALIASES and isinstance(node.ctx, ast.Load):
            yield node.lineno, _TYPING_ALIASES[node.id], f"typing.{node.id}"
        elif isinstance(node, ast.ImportFrom) and node.module == "typing":
            for a in node.names:
                if a.name in _TYPING_ALIASES:
                    yield node.lineno, _TYPING_ALIASES[a.name], f"from typing import {a.name}"


def check_deprecated(ws: Workspace, version: str, method: str | None = None) -> list[dict] | dict:
    """지원 종료(예정) API 사용 (DEP-*)."""
    return _run_check(ws, version, method, _deprecated)


# ---------------- 팀 스타일 ----------------

def _type_comment_lines(text: str) -> list[int]:
    lines = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT and re.match(r"#\s*type:", tok.string):
                lines.append(tok.start[0])
    except (tokenize.TokenError, IndentationError):
        pass
    return lines


def _is_log_call(node: ast.Call) -> bool:
    f = node.func
    return isinstance(f, ast.Attribute) and f.attr in _LOG_METHODS


def _style(tree: ast.Module, text: str, path: Path):
    log_args = {id(a) for n in ast.walk(tree) if isinstance(n, ast.Call) and _is_log_call(n) for a in n.args}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith("_"):
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            missing = [a.arg for a in args if a.annotation is None and a.arg not in ("self", "cls")]
            if missing or node.returns is None:
                what = (f"인자 {', '.join(missing)}" if missing else "") + (" 반환값" if node.returns is None else "")
                yield node.lineno, "STY-01", f"{node.name}: 타입 힌트 없음 ({what.strip()})"
            if ast.get_docstring(node) is None:
                yield node.lineno, "STY-04", f"{node.name}: docstring 없음"
        elif (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant)
              and isinstance(node.left.value, str) and id(node) not in log_args):
            yield node.lineno, "STY-03", "% 문자열 포맷"
        elif isinstance(node, ast.Call) and _call_name(node) == "print":
            yield node.lineno, "STY-05", "print()"
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            yield node.lineno, "STY-06", "맨 except:"
    for line in _type_comment_lines(text):
        yield line, "STY-02", "# type: 주석"


def check_style(ws: Workspace, version: str, method: str | None = None) -> list[dict] | dict:
    """팀 스타일 규칙 위반 (STY-*)."""
    return _run_check(ws, version, method, _style)


# ---------------- 보안 ----------------

def _sql_built(node: ast.AST) -> bool:
    """SQL 키워드가 든 문자열을 f-string·%·+로 조립하는가."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.Constant) and _SQL.search(str(v.value)) for v in node.values) and \
            any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod | ast.Add):
        parts = [n for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        return any(_SQL.search(p.value) for p in parts)
    return False


def _security(tree: ast.Module, text: str, path: Path):
    for node in ast.walk(tree):
        if _sql_built(node):
            yield node.lineno, "SEC-SQL-01", "SQL 문자열 조립 (f-string·%·+)"
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if _is_log_call(node):
                arg_names = {n.id for a in node.args[1:] for n in ast.walk(a) if isinstance(n, ast.Name)}
                arg_names |= {n.attr for a in node.args[1:] for n in ast.walk(a) if isinstance(n, ast.Attribute)}
                consts = {n.value for a in node.args[1:] for n in ast.walk(a)
                          if isinstance(n, ast.Constant) and isinstance(n.value, str)}
                if any("card" in a for a in arg_names | consts):
                    yield node.lineno, "SEC-LOG-02", "카드번호 로그"
                elif any(_PII.search(a) for a in arg_names | consts):
                    yield node.lineno, "SEC-LOG-01", "개인정보 로그"
                if arg_names & {"sql", "rows"}:
                    yield node.lineno, "SEC-LOG-03", "SQL·조회 결과 로그"
            if name.startswith("traceback.format"):
                yield node.lineno, "SEC-ERR-01", "트레이스백 노출"
            if name in ("hashlib.md5", "hashlib.sha1"):
                yield node.lineno, "SEC-CRY-01", name
            if name in ("eval", "exec", "pickle.loads", "pickle.load"):
                yield node.lineno, "SEC-INP-01", name


def _bandit(ws: Workspace, path: Path) -> list[tuple[int, str, str]]:
    proc = _run([sys.executable, "-m", "bandit", "-q", "-f", "json", str(path)], path.parent,
                config.SUBPROCESS_TIMEOUT_S)
    if proc is None:
        return []
    try:
        results = json.loads(proc.stdout or "{}").get("results", [])
    except json.JSONDecodeError:
        return []
    return [(r["line_number"], _BANDIT_RULES.get(r["test_id"], f"bandit-{r['test_id']}"),
             f"bandit {r['test_id']}: {r['issue_text']}") for r in results]


def check_security(ws: Workspace, version: str, method: str | None = None) -> list[dict] | dict:
    """bandit + 사내 보안 규칙 AST 검사 (SEC-*). 같은 줄·같은 규칙은 한 번만."""
    def finder(tree, text, path):
        seen = set()
        for line, rule_id, detail in [*_security(tree, text, path), *_bandit(ws, path)]:
            if (line, rule_id) not in seen:
                seen.add((line, rule_id))
                yield line, rule_id, detail
    return _run_check(ws, version, method, finder)


_FINDERS = {"deprecated": _deprecated, "style": _style}


def module_findings(ws: Workspace, version: str, module: str, kind: str) -> list[dict]:
    """모듈 파일 하나 전체의 검사 결과 (판정에서 범위를 직접 거를 때). kind: deprecated | style | security."""
    rel = f"{config.TARGET_SRC_SUBDIR}/{config.TARGET_PACKAGE}/{module}.py"
    path = ws.version_dir(version) / rel
    text = path.read_bytes().decode("utf-8")
    tree = ast.parse(text)
    if kind == "security":
        items, seen = [], set()
        for line, rule_id, detail in [*_security(tree, text, path), *_bandit(ws, path)]:
            if (line, rule_id) not in seen:
                seen.add((line, rule_id))
                items.append((line, rule_id, detail))
    else:
        items = list(_FINDERS[kind](tree, text, path))
    return [{"location": f"{rel}:{line}", "line": line, "rule_id": r, "detail": d} for line, r, d in items]
