"""티켓별 작업 디렉터리 관리 (PRD F1-3).

티켓마다 대상 프로젝트를 runs/<ticket_id>/ 아래 두 벌로 복사한다.
- original/: 원본 복사본. 수정 금지, run_tests(version="original")의 실행 위치
- work/: 작업본. apply_patch의 대상, run_tests(version="patched")의 실행 위치
- repro_tests/: 재현 테스트. 두 버전 모두에 대해 실행
target/ 원본은 복사할 때만 읽는다.
"""

import hashlib
import re
import shutil
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from sr_agent import config

_TICKET_ID = re.compile(config.TICKET_ID_PATTERN)
# reference: 판정용 정답 구현 버전 (workflow/reference.py가 만든다. LLM 도구에는 노출하지 않음)
# hidden: E2E 판정용 (수정본 + 숨긴 정답 테스트). 둘 다 LLM 도구에는 노출하지 않음
_VERSION_DIRS = {"original": config.ORIGINAL_DIRNAME, "patched": config.WORK_DIRNAME, "reference": "reference",
                 "hidden": "hidden"}


class WorkspaceError(ValueError):
    """작업본 규칙 위반 (잘못된 티켓 ID, 작업본 밖 경로 등)."""


@dataclass(frozen=True)
class Workspace:
    """한 티켓의 실행 디렉터리."""

    ticket_id: str
    root: Path

    @property
    def original(self) -> Path:
        return self.root / config.ORIGINAL_DIRNAME

    @property
    def work(self) -> Path:
        return self.root / config.WORK_DIRNAME

    @property
    def repro_tests(self) -> Path:
        return self.root / config.REPRO_DIRNAME

    @property
    def reports(self) -> Path:
        return self.root / config.REPORTS_DIRNAME

    @property
    def state_path(self) -> Path:
        return self.root / config.STATE_FILENAME

    def version_dir(self, version: str) -> Path:
        """"original" → original/, "patched" → work/."""
        try:
            return self.root / _VERSION_DIRS[version]
        except KeyError:
            raise WorkspaceError(f"알 수 없는 version: {version!r} (original|patched)") from None


def validate_ticket_id(ticket_id: str) -> str:
    """디렉터리 이름으로 안전한 티켓 ID인지 확인한다."""
    if not isinstance(ticket_id, str) or not _TICKET_ID.match(ticket_id):
        raise WorkspaceError(f"잘못된 티켓 ID: {ticket_id!r}")
    return ticket_id


def prepare_workspace(
    ticket_id: str,
    source: Path | None = None,
    runs_dir: Path | None = None,
    overwrite: bool = False,
) -> Workspace:
    """원본을 original/·work/ 두 벌로 복사하고 repro_tests/를 만든다.

    이미 만들어진 작업 디렉터리가 있으면 그대로 연다 (중단 후 이어서 실행).
    overwrite=True면 지우고 새로 복사한다.
    """
    validate_ticket_id(ticket_id)
    source = Path(source or config.TARGET_ROOT).resolve()
    if not source.is_dir():
        raise WorkspaceError(f"원본 디렉터리 없음: {source}")
    ws = Workspace(ticket_id, Path(runs_dir or config.RUNS_DIR).resolve() / ticket_id)

    if ws.root.exists():
        if not overwrite and ws.original.is_dir() and ws.work.is_dir():
            ws.repro_tests.mkdir(exist_ok=True)
            return ws
        shutil.rmtree(ws.root)

    ignore = shutil.ignore_patterns(*config.COPY_IGNORE_PATTERNS)
    ws.root.mkdir(parents=True)
    shutil.copytree(source, ws.original, ignore=ignore)
    shutil.copytree(source, ws.work, ignore=ignore)
    ws.repro_tests.mkdir()
    return ws


def open_workspace(ticket_id: str, runs_dir: Path | None = None) -> Workspace:
    """이미 만들어진 작업 디렉터리를 연다."""
    validate_ticket_id(ticket_id)
    ws = Workspace(ticket_id, Path(runs_dir or config.RUNS_DIR).resolve() / ticket_id)
    if not (ws.original.is_dir() and ws.work.is_dir()):
        raise WorkspaceError(f"작업 디렉터리 없음: {ws.root}")
    return ws


def _ignored(rel: Path) -> bool:
    return any(fnmatch(part, pattern) for part in rel.parts for pattern in config.COPY_IGNORE_PATTERNS)


def hash_tree(path: Path) -> dict[str, str]:
    """디렉터리 안 파일별 SHA-256. 키는 상대 경로(posix). 캐시 파일은 제외한다."""
    root = Path(path)
    hashes = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and not _ignored(rel):
            hashes[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


def compare_hashes(before: dict[str, str], after: dict[str, str]) -> dict:
    """두 hash_tree 결과를 비교한다."""
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    modified = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
    return {
        "unchanged": not (added or removed or modified),
        "added": added,
        "removed": removed,
        "modified": modified,
    }


def verify_original_intact(ws: Workspace, source: Path | None = None) -> dict:
    """original/ 복사본이 원본과 같은지 확인한다."""
    return compare_hashes(hash_tree(Path(source or config.TARGET_ROOT)), hash_tree(ws.original))


def resolve_inside(base_dir: Path, path: str | Path) -> Path:
    """base_dir 기준으로 경로를 풀고, base_dir 밖이면 WorkspaceError를 던진다."""
    base = Path(base_dir).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(base):
        raise WorkspaceError(f"작업본 밖 경로 접근 거부: {path}")
    return resolved


def resolve_in_workspace(ws: Workspace, path: str | Path, version: str = "patched") -> Path:
    """해당 버전 디렉터리 안의 경로만 허용한다."""
    return resolve_inside(ws.version_dir(version), path)
