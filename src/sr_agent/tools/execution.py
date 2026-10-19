"""코드 실행 도구 run_tests, run_diff (PRD F1-4, 7.3).

두 도구 모두 첫 인자로 현재 티켓의 Workspace를 받는다. LLM에 노출할 때는 ws를 바인딩해
PRD 7.3의 인자만 보이게 한다. 실패는 예외 대신 {"error": ...}로 돌려준다.
코드는 subprocess로 실행하고 타임아웃(기본 config.SUBPROCESS_TIMEOUT_S)을 건다.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from sr_agent import config
from sr_agent.tools.workspace import Workspace, WorkspaceError, resolve_inside

_METHOD = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")
_OUTSIDE_REPRO = re.compile(r"^(?:\.\./)+" + re.escape(config.REPRO_DIRNAME) + "/")


def _child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """자식 프로세스 환경. API 키 등 비밀 값은 넘기지 않는다."""
    env = {
        k: v for k, v in os.environ.items()
        if not any(marker in k.upper() for marker in config.SECRET_ENV_MARKERS)
    }
    env.update({
        "PYTHONPATH": str(config.SRC_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONHASHSEED": str(config.SEED),
    })
    env.update(extra or {})
    return env


def _run(cmd: list[str], cwd: Path, timeout: float, stdin: str | None = None,
         extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess | None:
    """subprocess 실행. 타임아웃이면 None."""
    try:
        return subprocess.run(
            cmd, cwd=cwd, env=_child_env(extra_env), input=stdin, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None


def _trim(text: str, limit: int = config.MAX_TRACE_CHARS) -> str:
    """긴 텍스트는 끝부분(오류 지점)만 남긴다."""
    if len(text) <= limit:
        return text
    return "...(앞부분 생략)\n" + text[-limit:]


def _test_targets(ws: Workspace, version: str, tests: list[str] | None) -> list[str]:
    """테스트 인자를 실행 경로로 바꾼다. 버전 디렉터리와 repro_tests/ 밖은 거부한다."""
    if tests is None:
        targets = [config.TARGET_TEST_SUBDIR]
        if any(ws.repro_tests.glob("test_*.py")):
            targets.append(str(ws.repro_tests))
        return targets
    if not isinstance(tests, list) or not tests:
        raise WorkspaceError("tests는 비어 있지 않은 리스트 또는 None이어야 함")

    targets = []
    for test in tests:
        path_part, sep, node = str(test).partition("::")
        norm = path_part.replace("\\", "/")
        if norm == config.REPRO_DIRNAME or norm.startswith(config.REPRO_DIRNAME + "/"):
            resolved = resolve_inside(ws.repro_tests, norm[len(config.REPRO_DIRNAME):].lstrip("/") or ".")
        else:
            resolved = resolve_inside(ws.version_dir(version), norm)
        if not resolved.exists():
            raise WorkspaceError(f"테스트 경로 없음: {test}")
        targets.append(str(resolved) + sep + node)
    return targets


def _display_name(r: dict, version_dir: Path, repro_dir: Path) -> str:
    """테스트 이름을 "tests/..." 또는 "repro_tests/..." 기준으로 통일한다."""
    nodeid = r["nodeid"].replace("\\", "/")
    if not r.get("path"):
        return _OUTSIDE_REPRO.sub(config.REPRO_DIRNAME + "/", nodeid)
    path = Path(r["path"]).resolve()
    if path.is_relative_to(repro_dir):
        rel = config.REPRO_DIRNAME + "/" + path.relative_to(repro_dir).as_posix()
    elif path.is_relative_to(version_dir):
        rel = path.relative_to(version_dir).as_posix()
    else:
        rel = path.name
    _, sep, name = nodeid.partition("::")
    return rel + sep + name


def _summarize(results: list[dict], version_dir: Path, repro_dir: Path) -> dict:
    passed = 0
    failed = []
    seen = set()
    for r in results:
        if r["outcome"] == "passed" and r["when"] == "call":
            passed += 1
        elif r["outcome"] == "failed" and r["nodeid"] not in seen:
            seen.add(r["nodeid"])
            failed.append({"test": _display_name(r, version_dir, repro_dir), "trace": _trim(r["longrepr"])})
    summary = {"passed": passed, "failed": failed[: config.MAX_FAILURES_REPORTED]}
    if len(failed) > config.MAX_FAILURES_REPORTED:
        summary["omitted"] = len(failed) - config.MAX_FAILURES_REPORTED
    return summary


def run_tests(ws: Workspace, version: str, tests: list[str] | None = None,
              timeout: float | None = None) -> dict:
    """original 또는 patched 버전에서 테스트를 실행한다.

    tests=None이면 대상 프로젝트 테스트 전체와 repro_tests/를 함께 돌린다.
    tests 항목은 버전 디렉터리 기준 경로(예: "tests/test_discount.py::test_x") 또는
    "repro_tests/..." 경로다.
    반환: {"passed": int, "failed": [{"test", "trace"}]} (실패가 많으면 "omitted" 추가)
    """
    timeout = timeout or config.SUBPROCESS_TIMEOUT_S
    try:
        version_dir = ws.version_dir(version)
        targets = _test_targets(ws, version, tests)
    except WorkspaceError as e:
        return {"error": str(e)}
    ini = version_dir / config.TARGET_CONFIG_FILE
    if not ini.is_file():
        return {"error": f"대상 프로젝트 설정 파일 없음: {ini.name}"}

    ws.reports.mkdir(exist_ok=True)
    report = ws.reports / f"pytest_{version}.json"
    report.unlink(missing_ok=True)
    cmd = [
        sys.executable, "-m", "pytest", "-q",
        "-p", "no:cacheprovider", "-p", "sr_agent.tools._pytest_report",
        "--rootdir", str(version_dir), "-c", str(ini), *targets,
    ]
    proc = _run(cmd, version_dir, timeout, extra_env={"SR_AGENT_REPORT": str(report)})
    if proc is None:
        return {"error": f"timeout: 테스트가 {timeout}초 안에 끝나지 않음"}
    if not report.is_file():
        return {"error": "pytest 실행 실패: " + _trim(proc.stderr or proc.stdout)}
    results = json.loads(report.read_text(encoding="utf-8"))["results"]
    return _summarize(results, version_dir.resolve(), ws.repro_tests.resolve())


def _validate_inputs(inputs) -> str | None:
    if not isinstance(inputs, list) or not inputs:
        return "inputs는 비어 있지 않은 리스트여야 함"
    if len(inputs) > config.MAX_DIFF_INPUTS:
        return f"inputs는 최대 {config.MAX_DIFF_INPUTS}개"
    for i, item in enumerate(inputs):
        if isinstance(item, dict):
            if not item.keys() <= {"args", "kwargs"}:
                return f"inputs[{i}]: dict는 args·kwargs 키만 허용"
            if not isinstance(item.get("args", []), list) or not isinstance(item.get("kwargs", {}), dict):
                return f"inputs[{i}]: args는 리스트, kwargs는 dict여야 함"
        elif not isinstance(item, list):
            return f"inputs[{i}]: 위치 인자 리스트 또는 {{args, kwargs}} dict여야 함"
    try:
        json.dumps(inputs)
    except (TypeError, ValueError) as e:
        return f"inputs를 JSON으로 직렬화할 수 없음: {e}"
    return None


def _shorten(value):
    """긴 결과 값은 문자열로 잘라 보여 준다 (비교는 잘리기 전 값으로 한다)."""
    text = json.dumps(value, ensure_ascii=False)
    if len(text) <= config.MAX_VALUE_CHARS:
        return value
    return text[: config.MAX_VALUE_CHARS] + "...(생략)"


def _run_version(ws: Workspace, version: str, payload: str, timeout: float) -> dict:
    version_dir = ws.version_dir(version)
    cmd = [sys.executable, "-m", "sr_agent.tools._diff_runner", str(version_dir / config.TARGET_SRC_SUBDIR)]
    proc = _run(cmd, version_dir, timeout, stdin=payload)
    if proc is None:
        return {"error": f"timeout: {timeout}초 안에 끝나지 않음"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": "실행 실패: " + _trim(proc.stderr or proc.stdout)}


def run_diff(ws: Workspace, method: str, inputs: list, timeout: float | None = None) -> list[dict] | dict:
    """같은 입력으로 원본과 수정본 함수를 각각 실행해 결과를 비교한다.

    method: "모듈.함수" (예: "discount.apply_coupon", 패키지 접두어는 생략 가능)
    inputs: 각 항목은 위치 인자 리스트 또는 {"args": [...], "kwargs": {...}}.
      특수 값 {"$datetime": ISO 문자열}, {"$date": "YYYY-MM-DD"},
      {"$db": {테이블: [행 dict, ...]}} (입력마다 새 인메모리 DB)
    반환: [{"input", "original", "patched", "same"}]
      original·patched는 {"ok": True, "value": ...} 또는 {"ok": False, "exception": "타입: 메시지"}
    """
    timeout = timeout or config.SUBPROCESS_TIMEOUT_S
    if not isinstance(method, str):
        return {"error": "method는 문자열이어야 함"}
    prefix = config.TARGET_PACKAGE + "."
    if method.startswith(prefix):
        method = method[len(prefix):]
    if not _METHOD.match(method):
        return {"error": f"method 형식 오류 (모듈.함수): {method!r}"}
    if problem := _validate_inputs(inputs):
        return {"error": problem}

    payload = json.dumps({"package": config.TARGET_PACKAGE, "method": method, "inputs": inputs})
    outputs = {}
    for version in ("original", "patched"):
        result = _run_version(ws, version, payload, timeout)
        if "error" in result:
            return {"error": f"{version}: {result['error']}"}
        outputs[version] = result["results"]

    return [
        {"input": inp, "original": _shorten(orig), "patched": _shorten(patched), "same": orig == patched}
        for inp, orig, patched in zip(inputs, outputs["original"], outputs["patched"], strict=True)
    ]
