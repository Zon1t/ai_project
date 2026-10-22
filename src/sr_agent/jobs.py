"""화면 밖에서 티켓을 처리하는 백그라운드 실행 (F6-2).

Streamlit은 버튼을 누르거나 새로고침하면 스크립트를 처음부터 다시 돌리며, 그 안에서 돌던 작업도 끊는다.
그래서 실행은 서버 프로세스의 스레드에서 하고, 화면은 진행 메시지와 state.json을 읽어 보여 주기만 한다.
같은 티켓은 동시에 하나만 실행한다 (접속한 사람이 여러 명이어도 서버 프로세스 하나에서 공유).
Streamlit에 의존하지 않아 테스트할 수 있다.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Job:
    """티켓 하나의 실행. messages는 (경과 초, 메시지)."""

    ticket_id: str
    started: float = field(default_factory=time.monotonic)
    messages: list[tuple[float, str]] = field(default_factory=list)
    done: bool = False
    result: str | None = None  # 끝난 뒤 상태 (예: approval_pending)
    error: str | None = None  # 실행이 예외로 끝났을 때 (크레딧 소진, 입력 오류 등)
    finished: float | None = None

    def progress(self, message: str) -> None:
        self.messages.append((time.monotonic() - self.started, message))

    @property
    def elapsed(self) -> float:
        return (self.finished or time.monotonic()) - self.started

    @property
    def last_message(self) -> str:
        return self.messages[-1][1] if self.messages else "시작하는 중"


class JobManager:
    """서버 프로세스에 하나. 티켓 ID별 최근 실행을 기억한다."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, ticket_id: str) -> Job | None:
        return self._jobs.get(ticket_id)

    def running(self, ticket_id: str) -> bool:
        job = self._jobs.get(ticket_id)
        return job is not None and not job.done

    def start(self, ticket_id: str, work: Callable[[Callable[[str], None]], str | None]) -> Job:
        """work(progress)를 스레드에서 실행한다. work는 끝난 상태 문자열을 돌려준다. 이미 실행 중이면 ValueError."""
        with self._lock:
            if self.running(ticket_id):
                raise ValueError(f"{ticket_id}는 이미 실행 중입니다")
            job = Job(ticket_id)
            self._jobs[ticket_id] = job

        def target() -> None:
            try:
                job.result = work(job.progress)
            except Exception as e:  # noqa: BLE001 - 스레드 밖으로 올리지 않고 화면에 보여 준다
                job.error = str(e) or type(e).__name__
            finally:
                job.finished = time.monotonic()
                job.done = True

        threading.Thread(target=target, name=f"ticket-{ticket_id}", daemon=True).start()
        return job

    def forget(self, ticket_id: str) -> None:
        """끝난 실행 기록을 지운다 (실행 중이면 그대로 둔다)."""
        with self._lock:
            if not self.running(ticket_id):
                self._jobs.pop(ticket_id, None)
