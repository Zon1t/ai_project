"""티켓 하나를 처리하는 동안 공유하는 실행 정보: 실행 ID, 로그, 토큰 예산, 작업본."""

from dataclasses import dataclass, field
from pathlib import Path

from sr_agent import config
from sr_agent.runlog import RunLog, new_run_id
from sr_agent.tools.workspace import Workspace


class BudgetExceeded(RuntimeError):
    """티켓당 토큰 상한을 넘음. 해당 티켓은 사람 검토로 넘긴다."""


@dataclass
class TokenBudget:
    """티켓당 토큰 상한 (입력+출력, 모든 에이전트 호출 합계)."""

    limit: int = config.TICKET_TOKEN_LIMIT
    used: int = 0

    def check(self) -> None:
        if self.used >= self.limit:
            raise BudgetExceeded(f"티켓 토큰 상한 초과: {self.used}/{self.limit}")

    def add(self, usage: dict) -> None:
        self.used += int(usage.get("total_tokens", 0))
        self.check()


@dataclass
class RunContext:
    """에이전트·도구 호출이 같이 쓰는 실행 정보."""

    ticket_id: str
    log: RunLog
    budget: TokenBudget = field(default_factory=TokenBudget)
    ws: Workspace | None = None

    @classmethod
    def new(cls, ticket_id: str, ws: Workspace | None = None, runs_dir: Path | None = None,
            token_limit: int = config.TICKET_TOKEN_LIMIT) -> "RunContext":
        """runs/<ticket_id>-<시각>.jsonl에 기록하는 새 실행."""
        return cls(ticket_id, RunLog(new_run_id(ticket_id), runs_dir), TokenBudget(token_limit), ws)
