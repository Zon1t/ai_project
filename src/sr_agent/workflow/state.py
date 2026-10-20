"""노드 사이에 넘기는 구조화 결과와 티켓 상태 (runs/<ticket_id>/state.json).

에이전트 출력 모델(AnalysisOutput, FixOutput)은 structured outputs 스키마로 쓰이므로 기본값 없이 모든 필드를 둔다.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

TicketType = Literal["defect", "standardize", "security"]
Status = Literal["new", "analyzing", "fixing", "approval_pending", "human_review", "approved", "rejected"]


class ReferenceImpl(BaseModel):
    """사람이 미리 써 둔 정답 함수 (판정 기준. 에이전트는 보지 않는다)."""

    method: str
    code: str


class AttackInput(BaseModel):
    """보안 판정: 공격 입력과 수정본이 내야 할 안전한 결과."""

    input: list | dict  # run_diff 입력 하나
    expected: object  # 기대 결과 값 (run_diff 표기)
    note: str = ""


class JudgeSpec(BaseModel):
    """판정 명세 (사람이 미리 정함, 에이전트는 보지 않음).

    - 결함 수정: method + strategy + reference(정답 함수) → 수정본 = 정답
    - 코드 표준화: method + strategy → 수정본 = 원본 (동작 완전 보존)
    - 보안: method + strategy(정상 입력) → 수정본 = 원본, attack_inputs → 안전한 결과
    """

    method: str  # Hypothesis로 호출할 진입 함수 (예: discount.apply_coupons)
    strategy: dict  # property_attack과 같은 strategy_spec
    reference: ReferenceImpl | None = None
    attack_inputs: list[AttackInput] = Field(default_factory=list)


class Ticket(BaseModel):
    """처리할 SR 티켓. judge는 판정 노드만 쓴다 (에이전트 입력에 넣지 않음)."""

    ticket_id: str
    type: TicketType
    title: str
    body: str
    scenario: str | None = None
    method: str | None = None  # 코드 표준화·보안: 진단이 알려 준 대상 함수 (분석 단계를 건너뜀)
    judge: JudgeSpec | None = None

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}"


class Evidence(BaseModel):
    kind: Literal["model", "history", "caller", "coverage", "test", "code", "rule"]
    ref: str  # history_id, 함수 이름, 테스트 이름 등 확인 가능한 식별자
    note: str


class AnalysisOutput(BaseModel):
    """분석 에이전트의 최종 결과 (F5-1)."""

    final_method: str  # 결함이 있는 함수 "모듈.함수"
    model_top3: list[str]  # predict_fault_location Top-3
    model_rank: int | None  # final_method의 모델 순위 (Top-k 밖이면 null)
    reason: str  # 이 함수로 판단한 이유
    disagreement: str  # 모델 1위와 다르면 그 이유, 같으면 빈 문자열
    evidence: list[Evidence]
    history_ids: list[str]
    repro_test_path: str  # repro_tests/test_*.py


class FixOutput(BaseModel):
    """수정 에이전트의 최종 결과 (F5-2)."""

    method: str
    patch_summary: str  # 무엇을 왜 바꿨는지 (규칙 ID·이력 ID 인용)
    rule_ids: list[str]
    history_ids: list[str]


class AttackFinding(BaseModel):
    """공격 에이전트가 찾은 문제 하나 (F5-3)."""

    kind: Literal["under_fix", "regression"]  # 수정 미달 / 회귀
    method: str  # run_diff로 호출한 함수
    input_json: str  # run_diff 입력 하나 (인자 리스트 또는 {"args", "kwargs"})
    expected_json: str  # 업무 규칙상 올바른 결과. 예외를 기대하면 {"$exception": "ValueError"}
    rule_id: str  # 근거 규칙 ID (없으면 빈 문자열)
    explanation: str


class AttackOutput(BaseModel):
    """공격 에이전트의 최종 결과."""

    findings: list[AttackFinding]
    attempts: list[str]  # 시도한 공격 요약
    summary: str


class VerifiedFinding(AttackFinding):
    """코드로 다시 실행해 확인한 발견. verified는 코드로 확인된 사실만 뜻한다."""

    original: dict | None = None
    patched: dict | None = None
    verified: bool = False
    check: str = ""  # 무엇을 확인했는지


class AttackReport(BaseModel):
    summary: str = ""
    attempts: list[str] = Field(default_factory=list)
    findings: list[VerifiedFinding] = Field(default_factory=list)
    saved_to_memory: int = 0
    error: str | None = None

    def verified(self) -> list[VerifiedFinding]:
        return [f for f in self.findings if f.verified]


class JudgeCheck(BaseModel):
    name: str
    passed: bool | None  # None = 아직 평가하지 않는 조건
    detail: str


class JudgeResult(BaseModel):
    passed: bool
    checks: list[JudgeCheck]

    def failures(self) -> list[JudgeCheck]:
        return [c for c in self.checks if c.passed is False]


class FixAttempt(BaseModel):
    number: int  # 1 = 첫 시도, 2~ = 재시도
    fix: FixOutput | None = None
    diff: str = ""
    attack: AttackReport | None = None
    judge: JudgeResult | None = None
    error: str | None = None


class TicketState(BaseModel):
    """티켓 진행 상태. 노드가 끝날 때마다 저장해 중단 후 이어서 실행한다."""

    ticket: Ticket
    status: Status = "new"
    analysis: AnalysisOutput | None = None
    repro_verified: bool = False
    analysis_tries: int = 0
    attempts: list[FixAttempt] = Field(default_factory=list)
    human_review_reason: str | None = None
    tokens_used: int = 0
    run_ids: list[str] = Field(default_factory=list)
    updated_at: str = ""

    def save(self, path: Path) -> None:
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")

    @classmethod
    def load(cls, path: Path) -> "TicketState | None":
        path = Path(path)
        if not path.is_file():
            return None
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    @property
    def passed(self) -> bool:
        return bool(self.attempts and self.attempts[-1].judge and self.attempts[-1].judge.passed)
