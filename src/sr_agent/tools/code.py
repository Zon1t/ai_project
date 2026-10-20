"""코드 조회 도구 read_method (PRD 7.3). 작업본(work/)의 함수 소스를 돌려준다."""

import ast

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


def read_method(ws: Workspace, method: str) -> str | dict:
    """작업본의 함수 소스. 첫 줄에 "# 파일:시작-끝" 위치를 붙인다.

    method: "모듈.함수" (예: "discount.apply_coupon", 패키지 접두어는 생략 가능)
    """
    if not isinstance(method, str) or (parsed := _split_method(method)) is None:
        return {"error": f"method 형식 오류 (모듈.함수): {method!r}"}
    module, names = parsed
    rel = f"{config.TARGET_SRC_SUBDIR}/{config.TARGET_PACKAGE}/{module}.py"
    try:
        path = resolve_inside(ws.work, rel)
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
