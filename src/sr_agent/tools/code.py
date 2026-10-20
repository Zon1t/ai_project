"""코드 조회·수정 도구 read_method, get_callers, apply_patch (PRD 7.3). 모두 작업본(work/) 기준."""

import ast
import difflib
import json
import textwrap
from pathlib import Path

from sr_agent import config
from sr_agent.tools.workspace import Workspace, WorkspaceError, resolve_inside

_MAX_SOURCE_CHARS = 8000


def _split_method(method: str) -> tuple[str, list[str]] | None:
    prefix = config.TARGET_PACKAGE + "."
    if method.startswith(prefix):
        method = method[len(prefix):]
    parts = method.split(".")
    if len(parts) < 2 or not all(p.isidentifier() for p in parts):
        return None
    return parts[0], parts[1:]


def find_function(tree: ast.Module, names: list[str]) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """모듈 최상위 함수 또는 클래스 메서드를 찾는다."""
    body = tree.body
    for i, name in enumerate(names):
        last = i == len(names) - 1
        kinds = (ast.FunctionDef, ast.AsyncFunctionDef) if last else (ast.ClassDef,)
        node = next((n for n in body if isinstance(n, kinds) and n.name == name), None)
        if node is None:
            return None
        if last:
            return node
        body = node.body
    return None


def module_path(ws: Workspace, module: str) -> tuple[str, Path]:
    """(대상 프로젝트 기준 상대 경로, 작업본 안 절대 경로)."""
    rel = f"{config.TARGET_SRC_SUBDIR}/{config.TARGET_PACKAGE}/{module}.py"
    return rel, resolve_inside(ws.work, rel)


def read_method(ws: Workspace, method: str) -> str | dict:
    """작업본의 함수 소스. 첫 줄에 "# 파일:시작-끝" 위치를 붙인다.

    method: "모듈.함수" (예: "discount.apply_coupon", 패키지 접두어는 생략 가능)
    """
    if not isinstance(method, str) or (parsed := _split_method(method)) is None:
        return {"error": f"method 형식 오류 (모듈.함수): {method!r}"}
    module, names = parsed
    try:
        rel, path = module_path(ws, module)
    except WorkspaceError as e:
        return {"error": str(e)}
    if not path.is_file():
        return {"error": f"모듈 없음: {module}"}
    text = path.read_bytes().decode("utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return {"error": f"작업본 문법 오류: {e}"}
    node = find_function(tree, names)
    if node is None:
        return {"error": f"함수 없음: {method}"}
    start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
    source = "\n".join(text.splitlines()[start - 1:node.end_lineno])
    if len(source) > _MAX_SOURCE_CHARS:
        source = source[:_MAX_SOURCE_CHARS] + "\n...(생략)"
    return f"# {rel}:{start}-{node.end_lineno}\n{source}"


# ---------------- get_callers ----------------

def _module_trees(ws: Workspace) -> dict[str, ast.Module]:
    pkg = ws.work / config.TARGET_SRC_SUBDIR / config.TARGET_PACKAGE
    trees = {}
    for p in sorted(pkg.glob("*.py")):
        try:
            trees[p.stem] = ast.parse(p.read_bytes().decode("utf-8"))
        except SyntaxError:
            continue
    return trees


def _functions_of(tree: ast.Module, module: str) -> dict[str, ast.AST]:
    funcs: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            funcs[f"{module}.{node.name}"] = node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    funcs[f"{module}.{node.name}.{item.name}"] = item
    return funcs


def _import_maps(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    """(별칭 → 패키지 모듈, 별칭 → 모듈.함수). 대상 패키지 안의 import만 본다."""
    pkg = config.TARGET_PACKAGE
    modules: dict[str, str] = {}
    names: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == pkg:
            for a in node.names:
                modules[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(pkg + "."):
            mod = node.module.split(".", 1)[1]
            for a in node.names:
                names[a.asname or a.name] = f"{mod}.{a.name}"
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith(pkg + ".") and a.asname:
                    modules[a.asname] = a.name.split(".", 1)[1]
    return modules, names


def call_graph(ws: Workspace) -> dict[str, list[str]]:
    """함수 → 그 함수를 직접 호출하는 함수 목록 (정적 분석, 작업본 기준)."""
    trees = _module_trees(ws)
    all_funcs = {name for module, tree in trees.items() for name in _functions_of(tree, module)}
    callers: dict[str, set[str]] = {}
    for module, tree in trees.items():
        modules, names = _import_maps(tree)
        for caller, func in _functions_of(tree, module).items():
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                target = None
                if isinstance(node.func, ast.Name):
                    target = names.get(node.func.id, f"{module}.{node.func.id}")
                elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    if node.func.value.id in modules:
                        target = f"{modules[node.func.value.id]}.{node.func.attr}"
                if target in all_funcs and target != caller:
                    callers.setdefault(target, set()).add(caller)
    return {k: sorted(v) for k, v in callers.items()}


def get_callers(ws: Workspace, method: str) -> list[str] | dict:
    """이 함수를 직접 호출하는 함수 목록 ("모듈.함수")."""
    if not isinstance(method, str) or (parsed := _split_method(method)) is None:
        return {"error": f"method 형식 오류 (모듈.함수): {method!r}"}
    module, names = parsed
    target = ".".join([module, *names])
    trees = _module_trees(ws)
    if target not in {n for m, t in trees.items() for n in _functions_of(t, m)}:
        return {"error": f"함수 없음: {method}"}
    return call_graph(ws).get(target, [])


# ---------------- apply_patch ----------------

def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """이름·인자 이름·기본값 (타입 힌트는 제외: 코드 표준화에서 힌트를 더할 수 있어야 한다)."""
    args = node.args
    names = [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
    star = [a.arg if a else None for a in (args.vararg, args.kwarg)]
    defaults = [ast.dump(d) for d in [*args.defaults, *(d for d in args.kw_defaults if d is not None)]]
    return json.dumps([node.name, names, star, defaults])


def _bound_names(node: ast.Import | ast.ImportFrom) -> set[str]:
    return {(a.asname or a.name).split(".")[0] for a in node.names}


def _used_names(tree: ast.Module) -> set[str]:
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def _parse_import(stmt: str) -> ast.Import | ast.ImportFrom:
    body = ast.parse(stmt).body
    if len(body) != 1 or not isinstance(body[0], ast.Import | ast.ImportFrom):
        raise ValueError(f"import 문이 아님: {stmt!r}")
    return body[0]


def _apply_imports(text: str, imports: dict | None) -> str:
    """같은 파일의 import 추가·삭제. 다른 코드가 아직 쓰는 import 삭제와 import 아닌 문장은 거부한다."""
    if not imports:
        return text
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    used = _used_names(tree)
    removals = []
    for stmt in imports.get("remove") or []:
        wanted = ast.unparse(_parse_import(stmt))
        node = next((n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)
                     and ast.unparse(n) == wanted), None)
        if node is None:
            raise ValueError(f"삭제할 import 없음: {stmt!r}")
        if still_used := _bound_names(node) & used:
            raise ValueError(f"아직 쓰는 import는 지울 수 없음: {sorted(still_used)}")
        removals.append(node)
    for node in sorted(removals, key=lambda n: -n.lineno):
        del lines[node.lineno - 1:node.end_lineno]
    adds = [ast.unparse(_parse_import(stmt)) + "\n" for stmt in imports.get("add") or []]
    if adds:
        tree = ast.parse("".join(lines))
        top_imports = [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]
        if top_imports:
            at = top_imports[-1].end_lineno
        else:
            first = tree.body[0] if tree.body else None
            at = first.end_lineno if isinstance(first, ast.Expr) else 0  # 모듈 docstring 다음
        lines[at:at] = adds
    return "".join(lines)


def replace_function(project_root: Path, method: str, new_code: str, imports: dict | None = None) -> dict:
    """project_root(대상 프로젝트 복사본)의 함수 하나를 new_code로 바꾸고, 같은 파일의 import만 추가·삭제한다.

    함수 이름·인자·기본값은 바꿀 수 없다. 컴파일에 실패하면 파일을 바꾸지 않는다.
    반환: {"applied", "compile_ok", "error", "diff"}
    """
    fail = {"applied": False, "compile_ok": False, "error": None, "diff": ""}
    if not isinstance(method, str) or (parsed := _split_method(method)) is None:
        return {**fail, "error": f"method 형식 오류 (모듈.함수): {method!r}"}
    if not isinstance(new_code, str):
        return {**fail, "error": "new_code는 문자열이어야 함"}
    module, names = parsed
    rel = f"{config.TARGET_SRC_SUBDIR}/{config.TARGET_PACKAGE}/{module}.py"
    try:
        path = resolve_inside(project_root, rel)
    except WorkspaceError as e:
        return {**fail, "error": str(e)}
    if not path.is_file():
        return {**fail, "error": f"모듈 없음: {module}"}
    original = path.read_bytes().decode("utf-8")
    node = find_function(ast.parse(original), names)
    if node is None:
        return {**fail, "error": f"함수 없음: {method}"}
    code = textwrap.dedent(new_code).strip("\n")
    try:
        new_tree = ast.parse(code)
    except SyntaxError as e:
        return {**fail, "error": f"new_code 문법 오류: {e}"}
    if len(new_tree.body) != 1 or not isinstance(new_tree.body[0], ast.FunctionDef | ast.AsyncFunctionDef):
        return {**fail, "error": "new_code는 함수 정의 하나만 담아야 함 (다른 함수·모듈 코드 수정 불가)"}
    if _signature(new_tree.body[0]) != _signature(node):
        return {**fail, "error": "함수 이름·인자·기본값은 바꿀 수 없음"}

    start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
    lines = original.splitlines(keepends=True)
    lines[start - 1:node.end_lineno] = [textwrap.indent(code, " " * node.col_offset) + "\n"]
    try:
        text = _apply_imports("".join(lines), imports)
        compile(text, str(path), "exec")
    except ValueError as e:
        return {**fail, "compile_ok": True, "error": str(e)}
    except SyntaxError as e:
        return {**fail, "error": f"컴파일 실패: {e}"}
    path.write_bytes(text.encode("utf-8"))
    diff = "".join(difflib.unified_diff(original.splitlines(keepends=True), text.splitlines(keepends=True),
                                        fromfile=f"a/{rel}", tofile=f"b/{rel}", n=1))
    return {"applied": True, "compile_ok": True, "error": None, "diff": diff[:4000]}


def apply_patch(ws: Workspace, method: str, new_code: str, imports: dict | None = None) -> dict:
    """작업본(work/)의 대상 함수 하나를 바꾼다 (규칙은 replace_function과 같음)."""
    return replace_function(ws.work, method, new_code, imports)
