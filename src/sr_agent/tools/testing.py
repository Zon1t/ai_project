"""테스트 관련 도구 write_repro_test, get_coverage (PRD 7.3).

- write_repro_test: runs/<ticket_id>/repro_tests/에만 쓴다. 기존 테스트(tests/)는 건드리지 않는다.
- get_coverage: 작업본에서 coverage.py를 테스트별 기록(dynamic_context=test_function)으로 돌려,
  해당 함수의 줄을 지나간 테스트를 찾는다. 작업본 내용이 같으면 측정 결과를 다시 쓴다.
"""

import ast
import hashlib
import json
import re
import sys
import threading

from sr_agent import config
from sr_agent.tools.code import _split_method, find_function, module_path
from sr_agent.tools.execution import _run, _trim
from sr_agent.tools.workspace import Workspace, WorkspaceError, hash_tree

_REPRO_NAME = re.compile(r"^[a-z][a-z0-9_]{0,48}$")
_COVERAGE_RC = "[run]\nsource = {src}\ndynamic_context = test_function\n"


def write_repro_test(ws: Workspace, name: str, code: str) -> dict:
    """재현 테스트 파일 repro_tests/test_<name>.py를 쓴다 (같은 이름이면 덮어씀).

    반환: {"written": bool, "path": "repro_tests/test_<name>.py"} 또는 {"error"}
    """
    if not isinstance(name, str) or not _REPRO_NAME.match(name.removeprefix("test_")):
        return {"error": f"name은 영문 소문자·숫자·_ (예: two_coupons_negative): {name!r}"}
    if not isinstance(code, str) or not code.strip():
        return {"error": "code가 비어 있음"}
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"error": f"문법 오류: {e}"}
    if not any(isinstance(n, ast.FunctionDef) and n.name.startswith("test") for n in tree.body):
        return {"error": "test로 시작하는 테스트 함수가 하나 이상 있어야 함"}
    filename = f"test_{name.removeprefix('test_')}.py"
    ws.repro_tests.mkdir(exist_ok=True)
    (ws.repro_tests / filename).write_text(code, encoding="utf-8", newline="\n")
    return {"written": True, "path": f"{config.REPRO_DIRNAME}/{filename}"}


def _context_to_test(context: str) -> str:
    """coverage 컨텍스트 "test_discount.test_x" → "tests/test_discount.py::test_x"."""
    module, _, func = context.rpartition(".")
    return f"{config.TARGET_TEST_SUBDIR}/{module.replace('.', '/')}.py::{func}" if module else context


_MEASURE_LOCK = threading.Lock()  # 동시에 도는 공격 라운드가 같은 데이터 파일을 함께 쓰지 않게


def _measure(ws: Workspace, timeout: float) -> tuple[str, str | None]:
    """작업본 전체 테스트를 coverage로 돌린다. 반환: (데이터 파일 경로, 오류). 같은 내용이면 재사용."""
    with _MEASURE_LOCK:
        return _measure_locked(ws, timeout)


def _measure_locked(ws: Workspace, timeout: float) -> tuple[str, str | None]:
    digest = hashlib.sha256(json.dumps(hash_tree(ws.work), sort_keys=True).encode()).hexdigest()[:16]
    ws.reports.mkdir(exist_ok=True)
    data = ws.reports / f"coverage_{digest}.data"
    if data.is_file():
        return str(data), None
    rc = ws.reports / "coveragerc"
    rc.write_text(_COVERAGE_RC.format(src=config.TARGET_SRC_SUBDIR), encoding="utf-8")
    cmd = [sys.executable, "-m", "coverage", "run", f"--rcfile={rc}", f"--data-file={data}",
           "-m", "pytest", "-q", "-p", "no:cacheprovider", config.TARGET_TEST_SUBDIR]
    proc = _run(cmd, ws.work, timeout)
    if proc is None:
        return "", f"timeout: coverage 측정이 {timeout}초 안에 끝나지 않음"
    if not data.is_file():
        return "", "coverage 측정 실패: " + _trim(proc.stderr or proc.stdout)
    return str(data), None


def get_coverage(ws: Workspace, method: str, timeout: float | None = None) -> list[dict] | dict:
    """이 함수의 줄을 실행한 기존 테스트. 많이 지나간 순으로 최대 config.MAX_COVERAGE_TESTS개.

    반환: [{"test": "tests/...::test_x", "lines_hit": int}]
    """
    from coverage import CoverageData

    if not isinstance(method, str) or (parsed := _split_method(method)) is None:
        return {"error": f"method 형식 오류 (모듈.함수): {method!r}"}
    module, names = parsed
    try:
        _, path = module_path(ws, module)
    except WorkspaceError as e:
        return {"error": str(e)}
    if not path.is_file():
        return {"error": f"모듈 없음: {module}"}
    try:
        node = find_function(ast.parse(path.read_bytes().decode("utf-8")), names)
    except SyntaxError as e:
        return {"error": f"작업본 문법 오류: {e}"}
    if node is None:
        return {"error": f"함수 없음: {method}"}

    data_file, error = _measure(ws, timeout or config.COVERAGE_TIMEOUT_S)
    if error:
        return {"error": error}
    data = CoverageData(data_file)
    data.read()
    measured = next((f for f in data.measured_files() if path.samefile(f)), None)
    if measured is None:
        return []
    body_lines = set(range(node.body[0].lineno, node.end_lineno + 1))
    hits: dict[str, int] = {}
    for line, contexts in data.contexts_by_lineno(measured).items():
        if line in body_lines:
            for ctx in contexts:
                if ctx:
                    hits[ctx] = hits.get(ctx, 0) + 1
    ranked = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))[: config.MAX_COVERAGE_TESTS]
    return [{"test": _context_to_test(ctx), "lines_hit": n} for ctx, n in ranked]
