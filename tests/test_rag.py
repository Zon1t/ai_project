"""RAG (F4-1, F4-2): 규칙 파일, 이력 문서의 누수 방지, 색인·검색, 도구 입력 가드. 임베딩은 가짜로 대체한다."""

import json
import re

import numpy as np
import pytest

from sr_agent import config
from sr_agent.datagen.tickets import load_jsonl
from sr_agent.ml.corpus import load_tickets
from sr_agent.rag.index import KnowledgeBase, build_index, keyword_hits, open_client
from sr_agent.rag.kb import RULE_FIELDS, RULE_ID_PATTERN, history_docs, history_id, leaked_history, load_rules
from sr_agent.tools import search as search_mod
from sr_agent.tools.search import search_history, search_rules
from tests.test_ml import _fake_embedder

RULES = load_rules()


# ---------------- 규칙 파일 ----------------

def test_rules_file_has_40_valid_rules():
    assert len(RULES) == 40
    ids = [r["rule_id"] for r in RULES]
    assert len(set(ids)) == 40 and all(RULE_ID_PATTERN.match(i) for i in ids)
    for r in RULES:
        assert all(isinstance(r[f], str) and r[f].strip() for f in RULE_FIELDS), r["rule_id"]
        assert r["category"] == r["rule_id"].split("-")[0]
        assert r["severity"] in {"HIGH", "MEDIUM", "LOW"}
        for pattern in r["keywords"]:
            re.compile(pattern)
    counts = {c: sum(r["category"] == c for r in RULES) for c in ("SEC", "DEP", "STY", "BIZ")}
    assert counts == {"SEC": 12, "DEP": 8, "STY": 6, "BIZ": 14}


def test_scenario_rules_exist():
    ids = {r["rule_id"] for r in RULES}
    assert {"BIZ-CPN-01", "BIZ-CPN-02", "SEC-SQL-01", "DEP-DT-01", "DEP-TZ-01", "STY-01"} <= ids


def test_s1_business_rule_not_written_in_target_code():
    """S1 근거 규칙은 규칙 KB에만 있고 대상 코드 주석·docstring에는 없다 (PRD 5장)."""
    discount = (config.TARGET_ROOT / "src/order_settlement/discount.py").read_text(encoding="utf-8")
    assert "직전 쿠폰" not in discount and "0원 아래" not in discount


@pytest.mark.parametrize(("code", "rule_id"), [
    ("sql = f\"SELECT * FROM customers WHERE name = '{name}' ORDER BY id\"", "SEC-SQL-01"),
    ("sql = \"SELECT * FROM t WHERE id = %s\" % order_id", "SEC-SQL-01"),
    ("order_dt = datetime.utcnow()", "DEP-DT-01"),
    ("kst = pytz.timezone(config.TIMEZONE_NAME)", "DEP-TZ-01"),
    ("def calc_due_date(order_dt=None):\n    pass", "STY-01"),
    ("    # type: (int, Dict) -> int", "STY-02"),
    ("return current_amount - discount", "BIZ-CPN-02"),
    ("logger.info(\"결제 요청: card=%s\", card_number)", "SEC-LOG-02"),
])
def test_keywords_match_scenario_code(code, rule_id):
    rule = next(r for r in RULES if r["rule_id"] == rule_id)
    assert keyword_hits(rule["keywords"], code)


def test_typed_function_does_not_match_sty01():
    rule = next(r for r in RULES if r["rule_id"] == "STY-01")
    assert not keyword_hits(rule["keywords"], "def calc_due_date(order_dt: datetime | None = None) -> date:")


# ---------------- 이력 문서와 누수 ----------------

def _t(mid, split, suffix="A", method="m.f"):
    return {"ticket_id": f"T-{mid}-{suffix}", "mutant_id": mid, "split": split, "title": f"제목{suffix}",
            "body": f"본문{suffix}", "fixed_method": method}


def _m(mid, inject_type="ast"):
    return {"mutant_id": mid, "method": "discount.f", "inject_type": inject_type, "operator": "ROR",
            "description": "[경계 처리] 설명", "original": "a <= b", "replacement": "a < b",
            "file": "src/order_settlement/discount.py", "lineno": 3}


def test_history_docs_only_train_and_one_per_mutant():
    tickets = [_t("M1", "train", "A"), _t("M1", "train", "B"), _t("M2", "test"), _t("M3", "holdout")]
    docs = history_docs(tickets, {m: _m(m) for m in ("M1", "M2", "M3")})
    assert [d["mutant_id"] for d in docs] == ["M1"]
    d = docs[0]
    assert d["history_id"] == history_id("M1") and d["ticket_ids"] == "T-M1-A,T-M1-B"
    assert d["ticket"] == "제목A: 본문A\n제목B: 본문B"
    assert "비교 연산자" in d["root_cause"] and "`a <= b`" in d["root_cause"] and "`a < b`" in d["root_cause"]
    assert "3행" in d["fix_summary"]
    assert leaked_history(docs, tickets) == []


def test_leaked_history_detects_non_train():
    tickets = [_t("M1", "train"), _t("M2", "test")]
    docs = history_docs([{**t, "split": "train"} for t in tickets], {m: _m(m) for m in ("M1", "M2")})
    assert leaked_history(docs, tickets) == ["M2"]


def test_llm_history_uses_description():
    doc = history_docs([_t("M1", "train")], {"M1": _m("M1", "llm")})[0]
    assert "[경계 처리] 설명" in doc["root_cause"]


def test_real_history_docs_exclude_test_and_holdout():
    """실제 데이터: 이력은 학습 분할 결함만, 평가·홀드아웃 결함은 하나도 없다."""
    tickets = load_tickets()
    docs = history_docs(tickets, {m["mutant_id"]: m for m in load_jsonl(config.MUTANTS_PATH)})
    train = {t["mutant_id"] for t in tickets if t["split"] == "train"}
    assert {d["mutant_id"] for d in docs} == train
    assert leaked_history(docs, tickets) == []


@pytest.mark.skipif(not config.RAG_MANIFEST_PATH.is_file(), reason="RAG 색인 생성 전")
def test_real_index_has_only_train_history():
    """실제 Chroma 색인: 이력 ID가 학습 분할 결함과 정확히 같다."""
    tickets = load_tickets()
    col = open_client().get_collection(config.HISTORY_COLLECTION)
    ids = set(col.get(include=[])["ids"])
    assert ids == {history_id(t["mutant_id"]) for t in tickets if t["split"] == "train"}
    not_train = {history_id(t["mutant_id"]) for t in tickets if t["split"] != "train"}
    assert not ids & not_train
    assert open_client().get_collection(config.RULES_COLLECTION).count() == 40


# ---------------- 색인·검색 (가짜 임베딩) ----------------

class ConstModel:
    def encode(self, texts, **kwargs):
        return np.ones((len(texts), 4), dtype=np.float32) / 2


@pytest.fixture
def kb(tmp_path):
    tickets_csv = tmp_path / "tickets.csv"
    mutants_path = tmp_path / "mutants.jsonl"
    rows = [_t("MUT-0001", "train", s) for s in "AB"] + [_t("MUT-0002", "train", s) for s in "AB"] + \
        [_t("MUT-0003", "test", s) for s in "AB"]
    header = ("ticket_id,mutant_id,title,body,stacktrace,has_stacktrace,reporter,fixed_method,inject_type,"
              "operator,n_failed,split")
    lines = [header] + [f"{r['ticket_id']},{r['mutant_id']},{r['title']},{r['body']},,False,QA,m.f,ast,ROR,1,"
                        f"{r['split']}" for r in rows]
    tickets_csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mutants_path.write_text("\n".join(json.dumps(_m(m)) for m in ("MUT-0001", "MUT-0002", "MUT-0003")),
                            encoding="utf-8")
    emb = _fake_embedder("bge-m3", tmp_path / "cache")
    emb._model = ConstModel()  # 모든 텍스트가 같은 벡터 → 순위는 코드 패턴 가산점으로만 정해진다
    chroma = tmp_path / "chroma"
    manifest = build_index(tickets_csv, mutants_path, config.RULES_PATH, chroma, emb)
    assert manifest["history"] == 2 and manifest["rules"] == 40
    return KnowledgeBase(chroma, emb)


def test_search_history_format_and_train_only(kb):
    result = kb.search_history("제목A: 본문A", top_k=5)
    assert {r["history_id"] for r in result} == {"HIS-0001", "HIS-0002"}  # test 결함(0003)은 없음
    for r in result:
        assert set(r) == {"history_id", "ticket", "method", "root_cause", "fix_summary", "score"}


@pytest.mark.parametrize(("code", "expected"), [
    ("def find_by_name(conn, name):\n    sql = f\"SELECT * FROM customers WHERE name = '{name}'\"\n"
     "    return db.query_all(conn, sql)", "SEC-SQL-01"),
    ("def calc_due_date(order_dt=None):\n    if order_dt is None:\n        order_dt = datetime.utcnow()\n"
     "    kst = pytz.timezone(config.TIMEZONE_NAME)", "DEP-DT-01"),
    ("def apply_coupon(order_amount, coupon, current_amount=None):\n"
     "    if not is_coupon_applicable(order_amount, coupon):\n        return current_amount\n"
     "    return current_amount - discount", "BIZ-CPN-01"),
])
def test_search_rules_pattern_bonus_puts_scenario_rule_on_top(kb, code, expected):
    result = kb.search_rules(code, top_k=10)
    flags = [r["matched_pattern"] for r in result]
    assert flags == sorted(flags, reverse=True)  # 패턴이 맞은 규칙이 모두 앞에 온다
    matched = [r["rule_id"] for r in result if r["matched_pattern"]]
    assert expected in matched
    assert result[0]["score"] == pytest.approx(1 + config.RULE_KEYWORD_BONUS)


def test_build_index_refuses_leaky_history(tmp_path, monkeypatch):
    import sr_agent.rag.index as index_mod
    monkeypatch.setattr(index_mod, "leaked_history", lambda docs, tickets: ["MUT-X"])
    result = build_index(chroma_dir=tmp_path / "c", embedder=_fake_embedder("bge-m3", tmp_path))
    assert result["error"].startswith("학습 외 결함")


def test_missing_index_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError, match="RAG 색인 없음"):
        KnowledgeBase(tmp_path / "empty", _fake_embedder("bge-m3", tmp_path))


# ---------------- 도구 ----------------

@pytest.mark.parametrize("call", [
    lambda: search_history("", 3), lambda: search_history("증상", 0), lambda: search_history("가" * 4001, 3),
    lambda: search_rules(None, 3), lambda: search_rules("x = 1", 11), lambda: search_rules("x" * 8001, 3),
])
def test_tool_input_guard(call):
    assert set(call()) == {"error"}


def test_tools_use_shared_kb_and_return_error_dict(kb, monkeypatch):
    monkeypatch.setattr(search_mod, "_kb", kb)
    assert len(search_history("제목A", 1)) == 1
    assert search_rules("datetime.utcnow()", 1)[0]["rule_id"] == "DEP-DT-01"

    def broken(*a):
        raise RuntimeError("색인 손상")
    monkeypatch.setattr(kb, "search_history", broken)
    assert search_history("제목A", 1) == {"error": "RuntimeError: 색인 손상"}
