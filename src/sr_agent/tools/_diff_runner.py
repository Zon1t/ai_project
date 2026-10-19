"""run_diff가 띄우는 자식 프로세스.

사용: python -m sr_agent.tools._diff_runner <대상 src 디렉터리>
stdin: {"package", "method", "inputs"} JSON → stdout: {"results": [...]} 또는 {"error": ...}
"""

import contextlib
import datetime as dt
import importlib
import io
import json
import re
import sys

_IDENT = re.compile(r"^[A-Za-z_]\w*$")


def _make_db(tables: dict, package: str):
    """대상 프로젝트 db 모듈로 인메모리 DB를 만들고 행을 넣는다."""
    db = importlib.import_module(f"{package}.db")
    conn = db.connect(":memory:")
    db.init_schema(conn)
    for table, rows in tables.items():
        for row in rows:
            if not _IDENT.match(table) or not all(_IDENT.match(c) for c in row):
                raise ValueError(f"$db 테이블·컬럼 이름 오류: {table}")
            columns = ", ".join(row)
            marks = ", ".join("?" * len(row))
            conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(row.values()))
    conn.commit()
    return conn


def _decode(value, package: str):
    if isinstance(value, dict):
        if value.keys() == {"$datetime"}:
            return dt.datetime.fromisoformat(value["$datetime"])
        if value.keys() == {"$date"}:
            return dt.date.fromisoformat(value["$date"])
        if value.keys() == {"$db"}:
            return _make_db(value["$db"], package)
        return {k: _decode(v, package) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v, package) for v in value]
    return value


def _encode(value):
    if isinstance(value, dt.datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, dt.date):
        return {"$date": value.isoformat()}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_encode(v) for v in value]
    if isinstance(value, set | frozenset):
        return sorted((_encode(v) for v in value), key=repr)
    if hasattr(value, "keys"):  # sqlite3.Row
        return {k: _encode(value[k]) for k in value.keys()}
    return {"$repr": repr(value)}


def _call(func, raw, package: str) -> dict:
    args, kwargs = (raw.get("args", []), raw.get("kwargs", {})) if isinstance(raw, dict) else (raw, {})
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            value = func(*_decode(args, package), **_decode(kwargs, package))
        return {"ok": True, "value": _encode(value)}
    except Exception as e:
        return {"ok": False, "exception": f"{type(e).__name__}: {e}"}


def main() -> None:
    sys.path.insert(0, sys.argv[1])
    req = json.loads(sys.stdin.read())
    package = req["package"]
    module_name, func_name = req["method"].rsplit(".", 1)
    try:
        func = getattr(importlib.import_module(f"{package}.{module_name}"), func_name)
    except AttributeError:
        out = {"error": f"메서드를 찾을 수 없음: {req['method']}"}
    except Exception as e:  # SyntaxError 등 import 실패
        out = {"error": f"모듈 import 실패: {type(e).__name__}: {e}"}
    else:
        if callable(func):
            out = {"results": [_call(func, raw, package) for raw in req["inputs"]]}
        else:
            out = {"error": f"호출할 수 없는 대상: {req['method']}"}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
