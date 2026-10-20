"""도구 등록과 에이전트별 허용 목록 (PRD 7.3).

- 도구 정의는 strict 함수 도구(strict: True, additionalProperties: False)로 만든다. strict 모드는 모든 인자를
  required로 요구하므로 선택 인자는 null 허용 타입으로 두고, null이면 파이썬 기본값을 쓴다.
- 작업본이 필요한 도구는 첫 인자 ws를 실행 시점에 바인딩한다. LLM에는 PRD 7.3의 인자만 보인다.
- 허용 목록 밖의 도구 호출과 잘못된 인자는 실행하지 않고 {"error": ...}를 결과로 돌려준다.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sr_agent import config
from sr_agent.tools.workspace import Workspace

# PRD 7.3 "사용 에이전트" 열. 아직 구현하지 않은 도구는 등록되는 대로 쓰인다.
AGENT_TOOLS: dict[str, tuple[str, ...]] = {
    # search_rules: 재현 테스트의 기대값을 업무 규칙으로 정하려고 분석에도 허용 (PRD v0.7)
    "analysis": ("predict_fault_location", "search_history", "get_callers", "get_coverage", "read_method",
                 "search_rules", "write_repro_test", "run_tests"),
    "fix": ("search_rules", "read_method", "apply_patch", "run_tests", "check_deprecated", "check_security",
            "check_style"),
    # read_method 허용은 PRD 12장 기본안 (경계값 설계에 코드가 필요)
    "attack": ("get_callers", "get_coverage", "read_method", "run_diff", "property_attack", "attack_memory"),
}


@dataclass(frozen=True)
class Tool:
    """LLM에 노출하는 도구 하나."""

    name: str
    description: str
    properties: dict[str, dict]
    func: Callable[..., Any]
    needs_ws: bool = False
    defaults: tuple[tuple[str, Any], ...] = ()  # null로 온 인자에 넣을 기본값

    def schema(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": self.properties, "required": list(self.properties),
                           "additionalProperties": False},
            "strict": True,
        }


REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    REGISTRY[tool.name] = tool
    return tool


def tools_for(agent: str) -> list[Tool]:
    """에이전트가 쓸 수 있고 이미 구현된 도구."""
    return [REGISTRY[name] for name in AGENT_TOOLS[agent] if name in REGISTRY]


def _summary(value: Any, limit: int = config.LOG_SUMMARY_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def to_output(result: Any) -> str:
    """도구 결과를 모델에 돌려줄 문자열로. 길면 자르고 잘렸다고 알린다."""
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    limit = config.TOOL_OUTPUT_CHARS
    if len(text) > limit:
        return text[:limit] + f"\n...(출력 {len(text) - limit}자 생략)"
    return text


def execute(name: str, arguments: str, allowed: set[str], ws: Workspace | None) -> Any:
    """도구 하나를 실행한다. 어떤 실패도 예외 대신 {"error": ...}로 돌려준다."""
    if name not in allowed or name not in REGISTRY:
        return {"error": f"이 에이전트는 '{name}' 도구를 쓸 수 없음"}
    tool = REGISTRY[name]
    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError as e:
        return {"error": f"인자 JSON 파싱 실패: {e}"}
    if not isinstance(args, dict) or set(args) != set(tool.properties):
        return {"error": f"인자는 {sorted(tool.properties)} 이어야 함"}
    for key, default in tool.defaults:
        if args.get(key) is None:
            args[key] = default
    if tool.needs_ws and ws is None:
        return {"error": "작업본 없이 실행할 수 없는 도구"}
    try:
        return tool.func(ws, **args) if tool.needs_ws else tool.func(**args)
    except Exception as e:  # noqa: BLE001 - 도구 오류도 모델이 읽고 대응할 수 있게 값으로 돌려준다
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------- 등록 (구현된 도구부터) ----------------

def _register_builtin() -> None:
    from sr_agent.tools.attack import attack_memory, property_attack, run_diff_json
    from sr_agent.tools.code import apply_patch, get_callers, read_method
    from sr_agent.tools.execution import run_tests
    from sr_agent.tools.predict import predict_fault_location
    from sr_agent.tools.search import search_history, search_rules
    from sr_agent.tools.testing import get_coverage, write_repro_test

    top_k = {"type": ["integer", "null"], "description": "결과 개수 (null이면 기본값)"}
    method = {"type": "string", "description": "모듈.함수 (예: discount.apply_coupon)"}
    register(Tool("get_callers", "이 함수를 직접 호출하는 함수 목록 (정적 분석).", {"method": method},
                  get_callers, needs_ws=True))
    register(Tool("get_coverage", "이 함수의 줄을 실행하는 기존 테스트와 지나간 줄 수.", {"method": method},
                  get_coverage, needs_ws=True))
    register(Tool(
        "write_repro_test",
        "재현 테스트를 repro_tests/test_<name>.py로 쓴다. order_settlement를 import하고 데이터는 테스트 안에서 "
        "만든다(기존 conftest 픽스처 사용 불가). 올바른 동작을 기대하도록 쓴다.",
        {"name": {"type": "string", "description": "파일 이름 (영문 소문자·숫자·_)"},
         "code": {"type": "string", "description": "pytest 테스트 코드 전체"}},
        write_repro_test, needs_ws=True,
    ))
    register(Tool(
        "apply_patch",
        "작업본의 대상 함수 하나를 new_code(함수 정의 전체)로 바꾼다. 함수 이름·인자·기본값은 유지해야 한다. "
        "같은 파일의 import 추가·삭제만 imports로 할 수 있다.",
        {"method": method,
         "new_code": {"type": "string", "description": "def로 시작하는 함수 정의 전체"},
         "imports": {"type": ["object", "null"], "description": "import 변경 (없으면 null)",
                     "properties": {"add": {"type": "array", "items": {"type": "string"}},
                                    "remove": {"type": "array", "items": {"type": "string"}}},
                     "required": ["add", "remove"], "additionalProperties": False}},
        apply_patch, needs_ws=True,
    ))
    register(Tool(
        "read_method", "작업본에서 함수 소스를 읽는다. 첫 줄에 파일과 줄 범위가 붙는다.",
        {"method": method},
        read_method, needs_ws=True,
    ))
    register(Tool(
        "predict_fault_location",
        "티켓 문장으로 결함 위치 후보 Top-k와 SHAP 근거를 예측한다. 참고 자료이며 최종 판단은 근거로 한다.",
        {"ticket_text": {"type": "string", "description": "티켓 제목과 본문 (스택트레이스가 있으면 포함)"},
         "top_k": top_k},
        predict_fault_location, defaults=(("top_k", 3),),
    ))
    register(Tool(
        "search_history", "증상과 비슷한 과거 장애·조치 이력을 찾는다. history_id를 근거로 인용한다.",
        {"query": {"type": "string", "description": "증상 문장"}, "top_k": top_k},
        search_history, defaults=(("top_k", 3),),
    ))
    register(Tool(
        "search_rules", "코드와 관련된 사내 규칙(보안·지원 종료 API·팀 스타일·업무)을 찾는다. rule_id를 인용한다.",
        {"code": {"type": "string", "description": "검토할 코드"}, "top_k": top_k},
        search_rules, defaults=(("top_k", 5),),
    ))
    # 형태가 자유로운 JSON(입력·전략·기록)은 strict 스키마로 표현할 수 없어 JSON 문자열로 받는다
    register(Tool(
        "run_diff", "같은 입력으로 원본과 수정본 함수를 각각 실행해 결과를 비교한다.",
        {"method": method,
         "inputs_json": {"type": "string", "description": "입력 목록 JSON. 각 입력은 인자 리스트 또는 "
                         '{"args": [...], "kwargs": {...}}. 날짜는 {"$date": "2024-01-31"}, '
                         '시각은 {"$datetime": "2024-01-31T16:00:00"}'}},
        run_diff_json, needs_ws=True,
    ))
    register(Tool(
        "property_attack", "Hypothesis로 입력을 많이 만들어 원본과 수정본을 비교하고, 다른 입력은 최소 사례로 줄인다.",
        {"method": method,
         "strategy_json": {"type": "string",
                           "description": '입력 생성 규칙 JSON: {"args": [spec...], "kwargs": {...}}'},
         "max_examples": {"type": ["integer", "null"], "description": "생성할 입력 수 (null이면 200)"}},
        lambda ws, method, strategy_json, max_examples: property_attack(ws, method, strategy_json, max_examples),
        needs_ws=True, defaults=(("max_examples", 200),),
    ))
    register(Tool(
        "attack_memory", "같은 유형 티켓에서 과거에 성공한 공격을 찾는다. 저장은 워크플로가 확인된 발견만 한다.",
        {"op": {"type": "string", "enum": ["search"]},
         "attack_type": {"type": "string", "enum": ["defect", "standardize", "security"],
                         "description": "티켓 유형"},
         "record_json": {"type": ["string", "null"],
                         "description": '{"method": "모듈.함수"} (선택, 같은 함수 공격을 앞에 둔다)'}},
        lambda op, attack_type, record_json: attack_memory(op, attack_type, record_json),
    ))
    register(Tool(
        "run_tests",
        "테스트를 실행한다. original은 원본 복사본, patched는 작업본에서 돌리며 재현 테스트를 함께 돌린다.",
        {"version": {"type": "string", "enum": ["original", "patched"]},
         "tests": {"type": ["array", "null"], "items": {"type": "string"},
                   "description": "tests/... 또는 repro_tests/... 경로 목록. null이면 전체"}},
        run_tests, needs_ws=True,
    ))


_register_builtin()
