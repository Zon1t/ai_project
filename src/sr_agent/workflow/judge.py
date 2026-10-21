"""판정 노드 (PRD F5-4, 7.2). 코드로만 판단한다 — LLM을 부르지 않는다.

코드 표준화: 동작 완전 보존(Hypothesis 200개 + 공격 에이전트 입력, 원본 = 수정본), 지원 종료 API 0건, 팀 스타일 0건,
  기존 테스트 + 대상 모듈의 DeprecationWarning을 오류로, 변경 범위.
보안: bandit·사내 규칙 0건, 공격 입력 무력화(시나리오의 기대 결과), 정상 입력 원본과 동일,
  공격 에이전트 입력에서 정답 구현과 동일, 기존 테스트, 변경 범위.
"0건"은 PRD 7.2 검사 범위대로 패치한 함수와 새로 생긴 import 줄의 결과만 센다.

결함 수정 유형의 통과 조건:
1. 재현 테스트가 원본에서 실패하고 수정본에서 통과
2. 기존 테스트가 수정본에서 모두 통과
3. 변경 범위: 대상 함수가 있는 파일만 바뀌고, 그 파일에서도 대상 함수와 import 외에는 그대로
4. 참조 구현 비교: Hypothesis 입력 200개에서 수정본 출력 = 정답 구현 출력 (판정 명세가 있을 때).
   불일치는 원본이 정답과 같았으면 회귀, 원본도 달랐으면 미달로 센다. 결함 영역 밖에서 원본과 같아야 한다는
   PRD 7.2 조건을 포함한다 (영역 밖 = 원본과 정답이 같은 입력). 명세가 없으면 미평가(None).
5. 공격 입력 재판정: 공격 에이전트가 제안한 입력 전부에서 수정본 출력 = 정답 구현 출력 (정답 구현이 있을 때).
"""

import ast
import json
import shutil

from sr_agent import config
from sr_agent.tools.attack import compare_inputs, compare_versions, input_key
from sr_agent.tools.checks import module_findings, orphaned_import_lines
from sr_agent.tools.code import _split_method, find_function, module_path
from sr_agent.tools.execution import _validate_inputs, run_diff, run_tests
from sr_agent.tools.workspace import Workspace, compare_hashes, hash_tree, remove_tree
from sr_agent.workflow.reference import build_reference
from sr_agent.workflow.state import AttackReport, JudgeCheck, JudgeResult, JudgeSpec, Ticket


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


def _short(value, limit: int = 160) -> str:
    import json

    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def hidden_tests_check(ws: Workspace, spec: JudgeSpec) -> JudgeCheck:
    """E2E: 에이전트에게 숨긴 정답 테스트를 수정본에서 돌린다 (작업본 + 대상 프로젝트 원래 테스트)."""
    dest = ws.root / "hidden"
    if dest.exists():
        remove_tree(dest)
    shutil.copytree(ws.work, dest, ignore=shutil.ignore_patterns(*config.COPY_IGNORE_PATTERNS))
    remove_tree(dest / config.TARGET_TEST_SUBDIR)
    shutil.copytree(config.TARGET_ROOT / config.TARGET_TEST_SUBDIR, dest / config.TARGET_TEST_SUBDIR,
                    ignore=shutil.ignore_patterns(*config.COPY_IGNORE_PATTERNS))
    tests = list(dict.fromkeys(t.split("[")[0] for t in spec.hidden_tests))
    result = run_tests(ws, "hidden", tests, timeout=config.JUDGE_TEST_TIMEOUT_S)
    ok = "error" not in result and not result["failed"] and result["passed"] > 0
    data = {} if "error" in result else {"passed": result["passed"],
                                         "failed": len(result["failed"]) + result.get("omitted", 0)}
    return JudgeCheck(name="hidden_tests_pass", passed=ok, detail=_summ(result), data=data)


def reference_check(ws: Workspace, spec: JudgeSpec) -> JudgeCheck:
    """수정본을 정답 구현과 Hypothesis 입력으로 비교한다 (코드만)."""
    if spec.strategy is None:
        return JudgeCheck(name="reference_property", passed=None, detail="입력 생성 규칙 없음 — Hypothesis 비교 미평가")
    src = config.TARGET_SRC_SUBDIR
    try:
        ref = build_reference(ws, spec)
    except ValueError as e:
        return JudgeCheck(name="reference_property", passed=False, detail=str(e))
    raw = compare_versions(spec.method, spec.strategy,
                           {"original": ws.original / src, "patched": ws.work / src, "reference": ref},
                           "reference", "patched", config.JUDGE_PROPERTY_EXAMPLES, ws.work)
    if "error" in raw:
        return JudgeCheck(name="reference_property", passed=False, detail=raw["error"])
    detail = (f"입력 {raw['examples']}개 중 정답과 다름 {raw['mismatches']}개 "
              f"(미달 {raw['under_fix']}, 회귀 {raw['regression']})")
    if raw["minimal"]:
        m = raw["minimal"]
        kind = {"under_fix": "미달", "regression": "회귀"}.get(m.get("kind"), "")
        # 정답 출력은 넣지 않는다 (판정 설명은 재시도하는 수정 에이전트에 넘어가고, 정답 구현은 에이전트에 숨긴다)
        detail += (f". 최소 사례({kind}) {_short(m['input'])}: 수정본 {_short(m['results']['patched'])}, "
                   f"원본 {_short(m['results']['original'])}")
    return JudgeCheck(name="reference_property", passed=raw["mismatches"] == 0, detail=detail,
                      data={k: raw[k] for k in ("examples", "mismatches", "under_fix", "regression")})


def existing_tests_check(ws: Workspace, deprecation_module: str | None = None) -> JudgeCheck:
    """기존 테스트 전체. deprecation_module이면 그 모듈이 내는 DeprecationWarning을 오류로 본다 (PRD 7.2)."""
    args = (["-W", f"error::DeprecationWarning:{config.TARGET_PACKAGE}.{deprecation_module}"]
            if deprecation_module else None)
    result = run_tests(ws, "patched", [config.TARGET_TEST_SUBDIR], timeout=config.JUDGE_TEST_TIMEOUT_S,
                       pytest_args=args)
    ok = "error" not in result and not result["failed"]
    detail = _summ(result) + (f" (-W error::DeprecationWarning:{deprecation_module})" if deprecation_module else "")
    data = {} if "error" in result else {"passed": result["passed"],
                                         "failed": len(result["failed"]) + result.get("omitted", 0)}
    return JudgeCheck(name="existing_tests_pass", passed=ok, detail=detail, data=data)


def scope_check(ws: Workspace, method: str) -> JudgeCheck:
    problems = scope_violations(ws, method)
    return JudgeCheck(name="change_scope", passed=not problems, detail="; ".join(problems) or "대상 함수만 변경")


def scoped_findings(ws: Workspace, method: str, kind: str) -> list[dict]:
    """PRD 7.2 검사 범위: 수정본에서 대상 함수 줄 범위 + 패치로 바뀐 import 줄의 결과만.

    바뀐 import = 새로 생긴 import + 패치 후 쓰지 않게 된 import (예: 사용처를 없앴는데 남은 import pytz).
    """
    module, names = _split_method(method)
    rel, path = module_path(ws, module)
    after = ast.parse(path.read_bytes().decode("utf-8"))
    before = ast.parse((ws.original / rel).read_bytes().decode("utf-8"))
    node = find_function(after, names)
    lines = set(range(node.lineno, node.end_lineno + 1)) if node else set()
    old_imports = {ast.unparse(n) for n in before.body if isinstance(n, ast.Import | ast.ImportFrom)}
    for n in after.body:
        if isinstance(n, ast.Import | ast.ImportFrom) and ast.unparse(n) not in old_imports:
            lines.update(range(n.lineno, n.end_lineno + 1))
    lines |= orphaned_import_lines(ws, module)
    return [f for f in module_findings(ws, "patched", module, kind) if f["line"] in lines]


def findings_check(ws: Workspace, method: str, kind: str, name: str) -> JudgeCheck:
    found = scoped_findings(ws, method, kind)
    detail = "0건" if not found else f"{len(found)}건: " + "; ".join(
        f"{f['location']} {f['rule_id']} {f['detail']}" for f in found[:5])
    return JudgeCheck(name=name, passed=not found, detail=detail, data={"findings": len(found)})


def preserved_check(ws: Workspace, spec: JudgeSpec, name: str) -> JudgeCheck:
    """Hypothesis 입력에서 수정본 출력 = 원본 출력 (동작 보존)."""
    src = config.TARGET_SRC_SUBDIR
    raw = compare_versions(spec.method, spec.strategy, {"original": ws.original / src, "patched": ws.work / src},
                           "original", "patched", config.JUDGE_PROPERTY_EXAMPLES, ws.work)
    if "error" in raw:
        return JudgeCheck(name=name, passed=False, detail=raw["error"])
    detail = f"입력 {raw['examples']}개 중 원본과 다름 {raw['mismatches']}개"
    if raw["minimal"]:
        m = raw["minimal"]
        detail += (f". 최소 사례 {_short(m['input'])}: 수정본 {_short(m['results']['patched'])}, "
                   f"원본 {_short(m['results']['original'])}")
    return JudgeCheck(name=name, passed=raw["mismatches"] == 0, detail=detail,
                      data={"examples": raw["examples"], "mismatches": raw["mismatches"],
                            "regression": raw["mismatches"]})


def attack_inputs_check(ws: Workspace, spec: JudgeSpec) -> JudgeCheck:
    """시나리오에 정한 공격 입력에서 수정본이 안전한 결과를 내는가."""
    if not spec.attack_inputs:
        return JudgeCheck(name="attack_neutralized", passed=None, detail="공격 입력 명세 없음")
    rows = run_diff(ws, spec.method, [a.input for a in spec.attack_inputs])
    if isinstance(rows, dict):
        return JudgeCheck(name="attack_neutralized", passed=False, detail=rows["error"])
    failed = [(a, r) for a, r in zip(spec.attack_inputs, rows, strict=True)
              if r["patched"] != {"ok": True, "value": a.expected}]
    detail = f"공격 입력 {len(rows)}개 중 무력화 {len(rows) - len(failed)}개"
    if failed:
        a, r = failed[0]
        detail += f". 실패: {a.note or _short(a.input)} → 수정본 {_short(r['patched'])}, 기대 {_short(a.expected)}"
    return JudgeCheck(name="attack_neutralized", passed=not failed, detail=detail,
                      data={"inputs": len(rows), "neutralized": len(rows) - len(failed), "under_fix": len(failed)})


def _result(checks: list[JudgeCheck]) -> JudgeResult:
    return JudgeResult(passed=all(c.passed is not False for c in checks), checks=checks)


def judge_defect(ws: Workspace, method: str, repro_tests: list[str], spec: JudgeSpec | None = None,
                 attack: AttackReport | None = None) -> JudgeResult:
    """결함 수정 유형 판정. spec에 정답 구현이 있으면 참조 구현 비교(Hypothesis, 공격 입력)까지 한다."""
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
    checks += [existing_tests_check(ws), scope_check(ws, method)]
    if spec is not None and spec.hidden_tests:
        checks.append(hidden_tests_check(ws, spec))
    if spec is None or spec.reference is None:
        checks.append(JudgeCheck(name="reference_property", passed=None,
                                 detail="판정 명세(정답 구현) 없음 — 결함 영역 밖 입력 동일성 미평가"))
    else:
        checks.append(reference_check(ws, spec))
    checks.append(attack_findings_check(ws, attack, spec, "reference"))
    return _result(checks)


def attack_findings_check(ws: Workspace, attack: AttackReport | None, spec: JudgeSpec | None,
                          baseline: str) -> JudgeCheck:
    """공격 에이전트가 제안한 입력 전부(확인 여부와 관계없이)를 다시 실행해 수정본을 기준과 비교한다.

    baseline: "reference"(결함 수정·보안, 정답 구현) 또는 "original"(코드 표준화, 동작 완전 보존).
    LLM은 입력만 제안하고, 같은지 다른지는 코드가 판단한다 (Hypothesis가 입력을 만드는 것과 같은 역할).
    LLM이 적은 기대값은 쓰지 않는다. 정답 기준이면 불일치를 미달(원본도 정답과 다름)·회귀(원본은 정답과 같음)로 나눈다.
    실행할 수 없는 입력(JSON·형식 오류, 없는 함수)은 세지 않는다. 판정 결과에 정답 출력은 넣지 않는다.
    """
    name = "attack_findings_rechecked"
    findings = attack.findings if attack else []
    if not findings:
        return JudgeCheck(name=name, passed=None, detail="공격이 제안한 입력 없음")
    src = config.TARGET_SRC_SUBDIR
    versions = {"original": ws.original / src, "patched": ws.work / src}
    if baseline == "reference":
        if spec is None or spec.reference is None:
            return JudgeCheck(name=name, passed=None, detail="정답 구현 없음 — 공격 입력 재판정 미평가")
        try:
            versions["reference"] = build_reference(ws, spec)
        except ValueError as e:
            return JudgeCheck(name=name, passed=False, detail=str(e))

    by_method: dict[str, list] = {}
    skipped = 0
    for f in findings:
        try:
            inp = json.loads(f.input_json)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if _validate_inputs([inp]):
            skipped += 1
            continue
        by_method.setdefault(f.method, []).append(inp)

    cases, under_fix, regression = [], 0, 0
    for method, inputs in by_method.items():
        raw = compare_inputs(method, inputs, versions, baseline, "patched", ws.work)
        if "error" in raw:
            skipped += len(inputs)
            continue
        cases += [(method, c) for c in raw["cases"]]
        under_fix += raw["under_fix"]
        regression += raw["mismatches"] if baseline == "original" else raw["regression"]
    if not cases:
        return JudgeCheck(name=name, passed=None, detail=f"실행할 수 있는 공격 입력 없음 (제외 {skipped}개)",
                          data={"inputs": 0, "skipped": skipped})

    label = "정답" if baseline == "reference" else "원본"
    failed = [(m, c) for m, c in cases if c["results"][baseline] != c["results"]["patched"]]
    detail = f"공격 입력 {len(cases)}개 중 {label}과 다름 {len(failed)}개"
    if baseline == "reference":
        detail += f" (미달 {under_fix}, 회귀 {regression})"
    if skipped:
        detail += f", 실행 불가로 제외 {skipped}개"
    if failed:
        m, c = failed[0]
        kind = {"under_fix": "미달", "regression": "회귀"}.get(c.get("kind"), "회귀")
        detail += (f". 예({kind}): {m}({_short(c['input'])}) → 수정본 {_short(c['results']['patched'])}, "
                   f"원본 {_short(c['results']['original'])}")
    # 재시도하는 수정 에이전트에 넘길 실패 입력 (정답 출력은 넣지 않는다)
    failed_inputs = [{"method": m, "input": c["input"], "kind": c.get("kind", "regression"),
                      "patched": c["results"]["patched"], "original": c["results"]["original"]}
                     for m, c in failed[:config.MAX_FAILED_ATTACK_INPUTS]]
    return JudgeCheck(name=name, passed=not failed, detail=detail,
                      data={"inputs": len(cases), "mismatches": len(failed), "under_fix": under_fix,
                            "regression": regression, "skipped": skipped, "failed_inputs": failed_inputs,
                            "failed_keys": [input_key(m, c["input"]) for m, c in failed]})


def judge_standardize(ws: Workspace, method: str, spec: JudgeSpec | None,
                      attack: AttackReport | None = None) -> JudgeResult:
    """코드 표준화: 동작 완전 보존(Hypothesis + 공격 회귀 입력), 지원 종료 API 0건, 팀 스타일 0건,
    대상 모듈 경고를 오류로 한 테스트, 변경 범위."""
    module = _split_method(method)[0]
    preserved = (preserved_check(ws, spec, "behavior_preserved") if spec
                 else JudgeCheck(name="behavior_preserved", passed=None, detail="판정 명세 없음"))
    return _result([
        preserved,
        attack_findings_check(ws, attack, spec, "original"),
        findings_check(ws, method, "deprecated", "deprecated_zero"),
        findings_check(ws, method, "style", "style_zero"),
        existing_tests_check(ws, deprecation_module=module),
        scope_check(ws, method),
    ])


def judge_security(ws: Workspace, method: str, spec: JudgeSpec | None,
                   attack: AttackReport | None = None) -> JudgeResult:
    """보안: bandit·사내 규칙 0건 + 공격 입력 무력화 + 정상 입력 원본과 동일 + 공격 에이전트 입력에서 정답과 동일."""
    checks = [findings_check(ws, method, "security", "security_zero")]
    if spec:
        checks += [attack_inputs_check(ws, spec), preserved_check(ws, spec, "normal_preserved")]
    else:
        checks.append(JudgeCheck(name="attack_neutralized", passed=None, detail="판정 명세 없음"))
    checks += [attack_findings_check(ws, attack, spec, "reference"), existing_tests_check(ws),
               scope_check(ws, method)]
    return _result(checks)


def judge_ticket(ws: Workspace, ticket: Ticket, method: str, repro_tests: list[str],
                 attack: AttackReport | None = None) -> JudgeResult:
    """티켓 유형별 판정 (F5-4)."""
    if ticket.type == "defect":
        return judge_defect(ws, method, repro_tests, ticket.judge, attack)
    if ticket.type == "standardize":
        return judge_standardize(ws, method, ticket.judge, attack)
    return judge_security(ws, method, ticket.judge, attack)
