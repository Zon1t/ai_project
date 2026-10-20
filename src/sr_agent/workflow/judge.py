"""판정 노드 (PRD F5-4, 7.2). 코드로만 판단한다 — LLM을 부르지 않는다.

결함 수정 유형의 통과 조건:
1. 재현 테스트가 원본에서 실패하고 수정본에서 통과
2. 기존 테스트가 수정본에서 모두 통과
3. 변경 범위: 대상 함수가 있는 파일만 바뀌고, 그 파일에서도 대상 함수와 import 외에는 그대로
4. 결함 영역 밖 Hypothesis 입력에서 원본과 출력 동일 → 공격 단계(4일차)에서 평가. 지금은 미평가(None)
"""

import ast

from sr_agent import config
from sr_agent.tools.code import _split_method, module_path
from sr_agent.tools.execution import run_tests
from sr_agent.tools.workspace import Workspace, compare_hashes, hash_tree
from sr_agent.workflow.state import JudgeCheck, JudgeResult


def _units(tree: ast.Module, skip: str) -> tuple[dict[str, str], list[str]]:
    """(함수 이름 → AST, 그 밖의 모듈 문장 AST 목록). skip 함수와 import는 뺀다."""
    funcs: dict[str, str] = {}
    others: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name != skip:
                funcs[node.name] = ast.dump(node)
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and f"{node.name}.{item.name}" != skip:
                    funcs[f"{node.name}.{item.name}"] = ast.dump(item)
            others.append(ast.dump(ast.ClassDef(name=node.name, bases=node.bases, keywords=node.keywords, body=[],
                                                decorator_list=node.decorator_list, type_params=[])))
        elif not isinstance(node, ast.Import | ast.ImportFrom):
            others.append(ast.dump(node))
    return funcs, others


def scope_violations(ws: Workspace, method: str) -> list[str]:
    """허용 범위 밖의 변경 목록 (비어 있으면 통과)."""
    parsed = _split_method(method)
    if parsed is None:
        return [f"method 형식 오류: {method}"]
    module, names = parsed
    rel, work_path = module_path(ws, module)
    diff = compare_hashes(hash_tree(ws.original), hash_tree(ws.work))
    problems = [f"추가된 파일: {p}" for p in diff["added"]] + [f"삭제된 파일: {p}" for p in diff["removed"]]
    problems += [f"대상 밖 파일 수정: {p}" for p in diff["modified"] if p != rel]
    if rel in diff["modified"]:
        before = ast.parse((ws.original / rel).read_bytes().decode("utf-8"))
        try:
            after = ast.parse(work_path.read_bytes().decode("utf-8"))
        except SyntaxError as e:
            return [*problems, f"수정본 문법 오류: {e}"]
        skip = ".".join(names)
        (f_before, o_before), (f_after, o_after) = _units(before, skip), _units(after, skip)
        problems += [f"다른 함수 수정: {module}.{n}" for n in sorted(f_before) if f_before[n] != f_after.get(n)]
        problems += [f"함수 추가: {module}.{n}" for n in sorted(set(f_after) - set(f_before))]
        if o_before != o_after:
            problems.append("import 외 모듈 최상단 코드 수정")
    return problems


def _summ(result: dict) -> str:
    if "error" in result:
        return result["error"]
    failed = [f["test"] for f in result["failed"]]
    text = f"통과 {result['passed']}, 실패 {len(failed) + result.get('omitted', 0)}"
    return text + (f": {failed[:3]}" if failed else "")


def judge_defect(ws: Workspace, method: str, repro_tests: list[str]) -> JudgeResult:
    """결함 수정 유형 판정."""
    timeout = config.JUDGE_TEST_TIMEOUT_S
    checks = []
    if not repro_tests:
        checks.append(JudgeCheck(name="repro_original_fails", passed=False, detail="재현 테스트 없음"))
    else:
        orig = run_tests(ws, "original", repro_tests, timeout=timeout)
        ok = "error" not in orig and bool(orig["failed"]) and all("::" in f["test"] for f in orig["failed"])
        checks.append(JudgeCheck(name="repro_original_fails", passed=ok, detail=_summ(orig)))
        patched = run_tests(ws, "patched", repro_tests, timeout=timeout)
        ok = "error" not in patched and not patched["failed"] and patched["passed"] > 0
        checks.append(JudgeCheck(name="repro_patched_passes", passed=ok, detail=_summ(patched)))
    existing = run_tests(ws, "patched", [config.TARGET_TEST_SUBDIR], timeout=timeout)
    ok = "error" not in existing and not existing["failed"]
    checks.append(JudgeCheck(name="existing_tests_pass", passed=ok, detail=_summ(existing)))
    problems = scope_violations(ws, method)
    checks.append(JudgeCheck(name="change_scope", passed=not problems,
                             detail="; ".join(problems) or "대상 함수만 변경"))
    checks.append(JudgeCheck(name="outside_region_unchanged", passed=None,
                             detail="결함 영역 밖 입력 동일성은 공격 단계(4일차)에서 평가"))
    return JudgeResult(passed=all(c.passed is not False for c in checks), checks=checks)
