"""pytest 실패 출력(run_tests의 trace) 가공.

pytest 출력에는 실패한 함수의 소스 전체가 찍혀 결함 코드가 그대로 보인다.
- 티켓 첨부용: 제품 코드 프레임만 남긴 Python traceback 형식 (파일·줄·함수·실패한 한 줄·예외)
- LLM 입력용: 테스트 쪽 프레임(호출과 기대값)과 오류 메시지만
"""

import re

from sr_agent import config

_SEPARATOR = re.compile(r"^(?:_ )+_\s*$")
_LOCATION = re.compile(r"^(?P<path>\S+?\.py):(?P<line>\d+): (?P<what>.+)$")
_DEF = re.compile(r"^\s*(?:async\s+)?def (\w+)\(")
_ADDRESS = re.compile(r" at 0x[0-9A-Fa-f]+")


def _frames(trace: str) -> list[list[str]]:
    frames: list[list[str]] = [[]]
    for line in trace.splitlines():
        if _SEPARATOR.match(line):
            frames.append([])
        else:
            frames[-1].append(_ADDRESS.sub("", line))
    return [f for f in frames if any(line.strip() for line in f)]


def _error_lines(frame: list[str]) -> list[str]:
    return [line[1:].strip() for line in frame if line.startswith("E ")]


def failure_excerpt(trace: str) -> str:
    """LLM 입력용: 테스트 프레임과 마지막 오류 메시지. 제품 코드 소스는 넣지 않는다."""
    frames = _frames(trace)
    if not frames:
        return ""
    excerpt = [line for line in frames[0] if not _LOCATION.match(line.strip())]
    if len(frames) > 1:
        excerpt += ["", "오류: " + " / ".join(_error_lines(frames[-1]))]
    return "\n".join(excerpt).strip()


def to_traceback(trace: str, package: str = config.TARGET_PACKAGE) -> str | None:
    """제품 코드 프레임만 남긴 traceback. 제품 코드 안에서 예외가 나지 않았으면 None."""
    frames = []
    error: list[str] = []
    for frame in _frames(trace):
        loc = next((m for line in reversed(frame) if (m := _LOCATION.match(line.strip()))), None)
        if loc is None:
            continue
        path = loc["path"].replace("\\", "/")
        marker = f"{package}/"
        if marker not in path:
            continue
        func = loc["what"][3:] if loc["what"].startswith("in ") else None
        if func is None:
            func = next((m[1] for line in frame if (m := _DEF.match(line))), "?")
        code = next((line.lstrip(">").strip() for line in reversed(frame) if line.startswith(">")), "")
        frames.append((package + "/" + path.split(marker, 1)[1], loc["line"], func, code))
        error = _error_lines(frame)
    if not frames or not error:
        return None
    lines = ["Traceback (most recent call last):"]
    for path, line, func, code in frames:
        lines.append(f'  File "{path}", line {line}, in {func}')
        if code:
            lines.append(f"    {code}")
    lines.extend(error)
    return "\n".join(lines)


def first_traceback(traces: list[dict]) -> str | None:
    """결함 버전의 실패 트레이스 중 제품 코드 traceback을 만들 수 있는 첫 번째."""
    for t in traces:
        if tb := to_traceback(t["trace"]):
            return tb
    return None
