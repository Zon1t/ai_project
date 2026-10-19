"""run_tests가 자식 pytest에 붙이는 플러그인. 결과를 SR_AGENT_REPORT 경로에 JSON으로 쓴다."""

import json
import os

_results: list[dict] = []
_paths: dict[str, str] = {}  # nodeid → 테스트 파일 절대 경로 (rootdir 밖 파일의 nodeid가 일정하지 않아서)


def pytest_collection_modifyitems(items):
    for item in items:
        _paths[item.nodeid] = str(item.path)


def pytest_runtest_logreport(report):
    if report.when == "call" or report.failed:
        _results.append({
            "nodeid": report.nodeid,
            "path": _paths.get(report.nodeid),
            "when": report.when,
            "outcome": report.outcome,
            "longrepr": report.longreprtext if report.failed else "",
        })


def pytest_collectreport(report):
    if report.failed:
        _results.append({
            "nodeid": report.nodeid or "<collection>",
            "when": "collect",
            "outcome": "failed",
            "longrepr": report.longreprtext,
        })


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get("SR_AGENT_REPORT")
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"exitstatus": int(exitstatus), "results": _results}, f, ensure_ascii=False)
