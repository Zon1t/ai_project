"""KB 문서 만들기: 장애·조치 이력(F4-1)과 사내 규칙(F4-2).

이력은 학습 분할(train) 결함만 쓴다 (CLAUDE.md 규칙 6). 결함 1개가 이력 1건이고,
티켓 2건(운영·QA)의 문장, 결함 함수, 원인, 조치를 담는다. 원인·조치는 결함 기록에서 코드로 만든다 (LLM 미사용).
"""

import json
import re
from collections import defaultdict
from pathlib import Path

from sr_agent import config
from sr_agent.datagen.mutate import OPERATORS

RULE_ID_PATTERN = re.compile(r"^(SEC|DEP|STY|BIZ)-(?:[A-Z]{2,3}-)?\d{2}$")
RULE_FIELDS = ("rule_id", "category", "title", "description", "bad_example", "good_example", "severity", "check")


def load_rules(path: Path | None = None) -> list[dict]:
    """data/rules/rules.json (규칙 40건)."""
    return json.loads(Path(path or config.RULES_PATH).read_text(encoding="utf-8"))


def rule_text(rule: dict) -> str:
    """임베딩할 규칙 텍스트: 제목·설명·나쁜 예·좋은 예."""
    return (f"{rule['rule_id']} {rule['title']}\n{rule['description']}\n"
            f"나쁜 예:\n{rule['bad_example']}\n좋은 예:\n{rule['good_example']}")


def _short(code: str, limit: int = 160) -> str:
    code = " ".join(code.split())
    return code if len(code) <= limit else code[:limit] + "…"


def root_cause(m: dict) -> str:
    """이력의 원인 설명. 결함 버전에서 바뀌었던 코드를 정상 코드와 대비한다."""
    if m["inject_type"] == "llm":
        what = m["description"]
    else:
        what = f"{OPERATORS.get(m['operator'], m['operator'])}"
    bad = _short(m["replacement"]) or "(코드 누락)"
    return f"{m['method']}: {what}. 정상 코드 `{_short(m['original'])}`가 `{bad}`로 되어 있었음"


def fix_summary(m: dict) -> str:
    """이력의 조치 내용."""
    return f"{m['file']} {m['lineno']}행을 `{_short(m['original'])}`로 복구하고 기존 테스트 통과 확인"


def history_id(mutant_id: str) -> str:
    return "HIS-" + mutant_id.removeprefix("MUT-")


def history_docs(tickets: list[dict], mutants: dict[str, dict]) -> list[dict]:
    """학습 분할 티켓으로 이력 문서를 만든다. 결함 1개 = 1건, 결함 ID 순."""
    by_mutant: dict[str, list[dict]] = defaultdict(list)
    for t in tickets:
        if t["split"] == "train":
            by_mutant[t["mutant_id"]].append(t)
    docs = []
    for mid in sorted(by_mutant):
        m = mutants[mid]
        ts = sorted(by_mutant[mid], key=lambda t: t["ticket_id"])
        docs.append({
            "history_id": history_id(mid),
            "mutant_id": mid,
            "ticket_ids": ",".join(t["ticket_id"] for t in ts),
            "ticket": "\n".join(f"{t['title']}: {t['body']}" for t in ts),
            "method": m["method"],
            "root_cause": root_cause(m),
            "fix_summary": fix_summary(m),
            "inject_type": m["inject_type"],
            "operator": m["operator"],
        })
    return docs


def leaked_history(docs: list[dict], tickets: list[dict]) -> list[str]:
    """이력에 들어간 학습 외(test·holdout) 결함 ID. 비어 있어야 정상."""
    not_train = {t["mutant_id"] for t in tickets if t["split"] != "train"}
    return sorted(d["mutant_id"] for d in docs if d["mutant_id"] in not_train)
