"""판정 3유형 (F5-4, PRD 7.2): 코드 표준화(S2)·보안(S3)의 통과해야 하는 패치 / 통과하면 안 되는 패치.

실제 대상 프로젝트와 data/scenarios.json의 판정 명세를 쓴다. 결함 수정(S1)은 test_attack.py, test_judge.py.
"""

import pytest

from sr_agent.run import load_ticket
from sr_agent.tools.checks import check_deprecated, check_security, check_style
from sr_agent.tools.code import apply_patch
from sr_agent.tools.workspace import prepare_workspace
from sr_agent.workflow.judge import judge_ticket

S2_DOC = '''    """정산 지급일: 주문일(한국 시간) 기준 월말 마감 후 영업일 5일째.

    order_dt: UTC naive datetime. 없으면 현재 시각.
    """
'''
S2_BODY_GOOD = '''    if order_dt is None:
        order_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    local = order_dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(config.TIMEZONE_NAME))
    month_end = date(local.year, local.month, date_utils.last_day_of_month(local.year, local.month))
    return date_utils.add_business_days(month_end, config.SETTLEMENT_BUSINESS_DAYS)
'''
S2_SIG = "def calc_due_date(order_dt: datetime | None = None) -> date:\n"
S2_IMPORTS = {"add": ["from datetime import timezone", "from zoneinfo import ZoneInfo"], "remove": ["import pytz"]}

S2_PATCHES = {
    "good": (S2_SIG + S2_DOC + S2_BODY_GOOD, S2_IMPORTS),
    # KST 변환을 빠뜨려 월 경계(UTC 15시 이후)에서 지급일이 바뀜
    "behavior_changed": (S2_SIG + S2_DOC + S2_BODY_GOOD.replace(
        "order_dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(config.TIMEZONE_NAME))", "order_dt"),
        {"add": ["from datetime import timezone"], "remove": ["import pytz"]}),
    # utcnow가 남음 (지원 종료 API)
    "utcnow_left": (S2_SIG + S2_DOC + S2_BODY_GOOD.replace(
        "datetime.now(timezone.utc).replace(tzinfo=None)", "datetime.utcnow()"), S2_IMPORTS),
    # 타입 힌트 없음 (팀 스타일)
    "no_type_hints": ("def calc_due_date(order_dt=None):\n" + S2_DOC + S2_BODY_GOOD, S2_IMPORTS),
}

S3_HEAD = '''def find_by_name(conn, name):
    # type: (object, str) -> List[Dict]
    """이름으로 고객 목록 조회 (동명이인 포함, id 순)."""
'''
S3_PATCHES = {
    "good": S3_HEAD + '''    sql = "SELECT * FROM customers WHERE name = ? ORDER BY id"
    return db.query_all(conn, sql, (name,))
''',
    # 원본 그대로 (f-string SQL)
    "unchanged": S3_HEAD + '''    sql = f"SELECT * FROM customers WHERE name = '{name}' ORDER BY id"
    logger.debug("find_by_name: %s", sql)
    return db.query_all(conn, sql)
''',
    # 따옴표만 이스케이프: 공격은 막지만 문자열 조립 규칙(SEC-SQL-01) 위반
    "escape_only": S3_HEAD + '''    safe = name.replace("'", "''")
    sql = f"SELECT * FROM customers WHERE name = '{safe}' ORDER BY id"
    return db.query_all(conn, sql)
''',
    # 바인딩은 했지만 SQL 문을 로그에 남김 (SEC-LOG-03)
    "logs_sql": S3_HEAD + '''    sql = "SELECT * FROM customers WHERE name = ? ORDER BY id"
    logger.debug("find_by_name: %s", sql)
    return db.query_all(conn, sql, (name,))
''',
    # 바인딩은 했지만 정렬이 바뀜 (정상 입력 결과 회귀)
    "order_changed": S3_HEAD + '''    sql = "SELECT * FROM customers WHERE name = ? ORDER BY id DESC"
    return db.query_all(conn, sql, (name,))
''',
}


def _judge(tmp_path, ticket_id, code, imports=None):
    ticket = load_ticket(ticket_id)
    ws = prepare_workspace(ticket_id, runs_dir=tmp_path)
    result = apply_patch(ws, ticket.method, code, imports)
    assert result["applied"], result["error"]
    return judge_ticket(ws, ticket, ticket.method, []), ws


def _checks(result):
    return {c.name: c.passed for c in result.checks}


@pytest.mark.parametrize(("name", "failing"), [
    ("good", set()),
    ("behavior_changed", {"behavior_preserved", "existing_tests_pass"}),  # 월 경계 테스트도 잡음
    ("utcnow_left", {"deprecated_zero", "existing_tests_pass"}),
    ("no_type_hints", {"style_zero"}),
])
def test_standardize_s2(tmp_path, name, failing):
    code, imports = S2_PATCHES[name]
    result, _ = _judge(tmp_path, "SR-2077", code, imports)
    checks = _checks(result)
    assert result.passed is (not failing)
    assert {k for k, v in checks.items() if v is False} == failing, result.checks
    assert set(checks) == {"behavior_preserved", "deprecated_zero", "style_zero", "existing_tests_pass",
                           "change_scope"}


@pytest.mark.parametrize(("name", "failing"), [
    ("good", set()),
    ("unchanged", {"security_zero", "attack_neutralized"}),
    ("escape_only", {"security_zero"}),
    ("logs_sql", {"security_zero"}),
    ("order_changed", {"normal_preserved", "existing_tests_pass"}),  # 동명이인 순서 테스트도 잡음
])
def test_security_s3(tmp_path, name, failing):
    if name == "unchanged":
        ticket = load_ticket("SR-2103")
        ws = prepare_workspace("SR-2103", runs_dir=tmp_path)
        result = judge_ticket(ws, ticket, ticket.method, [])
    else:
        result, _ = _judge(tmp_path, "SR-2103", S3_PATCHES[name])
    checks = _checks(result)
    assert result.passed is (not failing)
    assert {k for k, v in checks.items() if v is False} == failing, result.checks
    assert set(checks) == {"security_zero", "attack_neutralized", "normal_preserved", "existing_tests_pass",
                           "change_scope"}


def test_original_s2_fails_every_static_check(tmp_path):
    ticket = load_ticket("SR-2077")
    ws = prepare_workspace("SR-2077", runs_dir=tmp_path)
    checks = _checks(judge_ticket(ws, ticket, ticket.method, []))
    assert checks["deprecated_zero"] is False and checks["style_zero"] is False
    assert checks["existing_tests_pass"] is False  # test_due_date_default_now: utcnow 경고가 오류
    assert checks["behavior_preserved"] is True  # 아무것도 안 바꿨으니 동작은 같음


def test_scope_counts_only_patched_function(tmp_path):
    """다른 함수의 utcnow·f-string SQL(다른 티켓 재료)은 세지 않는다 (PRD 7.2 검사 범위)."""
    code, imports = S2_PATCHES["good"]
    result, ws = _judge(tmp_path, "SR-2077", code, imports)
    assert result.passed
    assert len(check_deprecated(ws, "patched")) > 0  # 프로젝트 전체에는 여전히 있음


def test_check_tools(tmp_path):
    ws = prepare_workspace("SR-CHK", runs_dir=tmp_path)
    dep = check_deprecated(ws, "original", "settlement.calc_due_date")
    assert {f["rule_id"] for f in dep} == {"DEP-DT-01", "DEP-TZ-01", "DEP-TZ-02"}
    assert [f["rule_id"] for f in check_style(ws, "original", "settlement.calc_due_date")] == ["STY-01"]
    sec = {f["rule_id"] for f in check_security(ws, "original", "customer_dao.find_by_name")}
    assert sec == {"SEC-SQL-01", "SEC-LOG-03"}
    style_all = {f["rule_id"] for f in check_style(ws, "original", "discount.apply_coupons")}
    assert style_all == {"STY-01", "STY-02"}
    assert "error" in check_style(ws, "original", "nomodule.f")
    assert "error" in check_deprecated(ws, "unknown")  # 잘못된 version


def test_orphaned_deprecated_import_counts(tmp_path):
    """함수 안 pytz 사용은 없앴지만 import pytz를 남긴 패치는 통과하면 안 된다."""
    code, imports = S2_PATCHES["good"]
    kept = {"add": imports["add"], "remove": []}
    result, ws = _judge(tmp_path, "SR-2077", code, kept)
    checks = _checks(result)
    assert checks["deprecated_zero"] is False and not result.passed
    assert "import pytz" in next(c.detail for c in result.checks if c.name == "deprecated_zero")
    found = check_deprecated(ws, "patched", "settlement.calc_due_date")
    assert [f["rule_id"] for f in found] == ["DEP-TZ-01"] and "쓰지 않는 import" in found[0]["detail"]
    # 원본에서도 안 쓰던 import(typing Tuple은 # type: 주석에서만 씀)는 세지 않는다
    assert all("typing" not in f["detail"] for f in found)
