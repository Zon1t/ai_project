"""후보 함수와 티켓 데이터 불러오기.

후보 함수는 대상 프로젝트 원본(target/)의 모든 함수다. 결함 버전과는 한 토큰만 다르므로 원본 코드를 쓴다.
"""

import ast
import csv
from dataclasses import dataclass
from pathlib import Path

from sr_agent import config
from sr_agent.datagen.mutate import package_files, read_source

_BRANCHES = (ast.If, ast.For, ast.While, ast.IfExp, ast.ExceptHandler, ast.comprehension, ast.With, ast.Assert)


@dataclass(frozen=True)
class Candidate:
    """순위를 매길 후보 함수."""

    method: str  # "모듈.함수"
    module: str
    name: str
    source: str
    module_doc: str
    loc: int
    complexity: int

    @property
    def document(self) -> str:
        """TF-IDF·임베딩에 넣는 텍스트: 모듈 설명 + 함수 소스(docstring 포함)."""
        return f"# {self.module}: {self.module_doc}\n{self.source}"


def complexity(node: ast.AST) -> int:
    """분기 수 기반 복잡도 (1 + 분기문 + 불리언 연산의 추가 피연산자)."""
    count = 1
    for n in ast.walk(node):
        if isinstance(n, _BRANCHES):
            count += 1
        elif isinstance(n, ast.BoolOp):
            count += len(n.values) - 1
    return count


def load_candidates(target_root: Path | None = None) -> list[Candidate]:
    """대상 패키지의 함수 목록 (모듈 최상위 함수와 클래스 메서드)."""
    root = Path(target_root or config.TARGET_ROOT)
    candidates = []
    for path in package_files(root):
        text = read_source(path)
        tree = ast.parse(text)
        module = path.stem
        doc = (ast.get_docstring(tree) or "").splitlines()
        module_doc = doc[0] if doc else ""
        nodes = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                nodes.append((node.name, node))
            elif isinstance(node, ast.ClassDef):
                nodes += [(f"{node.name}.{n.name}", n) for n in node.body
                          if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
        for name, node in nodes:
            candidates.append(Candidate(
                method=f"{module}.{name}", module=module, name=name.split(".")[-1],
                source=ast.get_source_segment(text, node) or "", module_doc=module_doc,
                loc=node.end_lineno - node.lineno + 1, complexity=complexity(node),
            ))
    return candidates


def load_tickets(csv_path: Path | None = None) -> list[dict]:
    """tickets.csv. has_stacktrace는 bool, n_failed는 int로 바꾼다."""
    with open(Path(csv_path or config.TICKETS_CSV_PATH), encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["has_stacktrace"] = r["has_stacktrace"] in ("True", "true", "1", True)
        r["n_failed"] = int(r["n_failed"])
    return rows


def ticket_text(ticket: dict) -> str:
    """모델 입력 텍스트: 제목 + 본문 + 스택트레이스(있으면)."""
    parts = [ticket["title"], ticket["body"]]
    if ticket.get("stacktrace"):
        parts.append(ticket["stacktrace"])
    return "\n".join(parts)
