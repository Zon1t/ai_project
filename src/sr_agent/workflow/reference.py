"""참조 구현(정답) 버전 만들기. 판정 노드가 수정본과 비교할 기준이다.

runs/<ticket_id>/reference/ = original/ 복사본에 정답 함수를 끼운 것. 에이전트는 이 폴더를 보지 않는다.
- 시연 시나리오: data/scenarios.json의 judge.reference (사람이 쓴 정답 함수)
- E2E 결함: 결함을 심기 전 원본 코드 자체가 정답 (4일차 E2E에서 사용)
"""

import shutil
from pathlib import Path

from sr_agent import config
from sr_agent.tools.code import replace_function
from sr_agent.tools.workspace import Workspace
from sr_agent.workflow.state import JudgeSpec

REFERENCE_DIRNAME = "reference"


def build_reference(ws: Workspace, spec: JudgeSpec) -> Path:
    """참조 버전을 새로 만들고 그 src 경로를 돌려준다."""
    dest = ws.root / REFERENCE_DIRNAME
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(ws.original, dest, ignore=shutil.ignore_patterns(*config.COPY_IGNORE_PATTERNS))
    result = replace_function(dest, spec.reference.method, spec.reference.code)
    if not result["applied"]:
        raise ValueError(f"참조 구현 적용 실패: {result['error']}")
    return dest / config.TARGET_SRC_SUBDIR
