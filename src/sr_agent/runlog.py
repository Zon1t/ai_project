"""실행 로그 runs/<run_id>.jsonl (PRD F7-1). LLM 호출·도구 호출마다 한 줄씩 남긴다."""

import json
import threading
from datetime import datetime
from pathlib import Path

from sr_agent import config


def new_run_id(prefix: str) -> str:
    """사람이 읽을 수 있는 실행 ID (예: ticketgen-20261020-101500)."""
    return f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}"


class RunLog:
    """스레드 안전한 JSONL 기록기. 키·비밀 값은 넘기지 않는다 (호출하는 쪽 책임)."""

    def __init__(self, run_id: str, runs_dir: Path | None = None):
        self.run_id = run_id
        self.path = Path(runs_dir or config.RUNS_DIR) / f"{run_id}.jsonl"
        self._lock = threading.Lock()

    def write(self, **record) -> None:
        line = {"ts": datetime.now().isoformat(timespec="seconds"), "run_id": self.run_id, **record}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
