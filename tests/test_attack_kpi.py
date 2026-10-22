"""공격 탐지율 KPI (PRD 2장): 불완전 패치 확인, 탐지 판정, 결과 표. 공격 에이전트는 가짜."""

import json

import pytest

from sr_agent import ablation, attack_kpi, config
from sr_agent.agents.loop import AgentResult
from sr_agent.workflow import attack as attack_mod
from sr_agent.workflow.state import AttackFinding, AttackOutput


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma")
    return tmp_path / "runs"


def _case(case_id: str) -> dict:
    return next(c for c in attack_kpi.load_patches() if c["case_id"] == case_id)


def test_patches_file_is_well_formed():
    cases = attack_kpi.load_patches()
    assert len({c["case_id"] for c in cases}) == len(cases) >= config.ATTACK_KPI_CASES
    assert {c["flaw"] for c in cases} == {"under_fix", "regression"}
    for c in cases:
        assert c["code"].startswith("def ") and c["probes"] and c["method"].split(".")[1] in c["code"]


def test_flawed_check_accepts_incomplete_and_rejects_correct_patch(runs):
    case = _case("K01")  # 0원 하한만 넣고 최소 주문 금액 판단은 그대로
    ticket, ws = attack_kpi.prepare_case(case, runs)
    flawed = attack_kpi.check_flawed(case, ticket, ws)
    assert flawed["ok"] and flawed["probe"]["mismatches"] == 1
    correct = {**case, "code": ticket.judge.reference.code}  # 정답 구현 그대로면 불완전하지 않음
    ticket, ws = attack_kpi.prepare_case(correct, runs)
    result = attack_kpi.check_flawed(correct, ticket, ws)
    assert not result["ok"] and "기준과 결과가 같음" in result["problems"][0]


def test_attack_case_detects_when_attack_input_differs_from_reference(runs, monkeypatch):
    case = _case("K01")
    ticket, ws = attack_kpi.prepare_case(case, runs)
    assert attack_kpi.check_flawed(case, ticket, ws)["ok"]
    probe = json.dumps(case["probes"][0], ensure_ascii=False)
    found = AttackFinding(kind="under_fix", method=case["probe_method"], input_json=probe, expected_json="20000",
                          rule_id="BIZ-CPN-01", explanation="두 번째 쿠폰은 현재 금액 기준으로 판단")
    inputs, findings = [], [found]

    def fake_agent(client, spec, user_input, ctx):
        inputs.append(json.loads(user_input))
        return AgentResult(output=AttackOutput(findings=findings, attempts=[], summary=""), model="fake", turns=1)
    monkeypatch.setattr(attack_mod, "run_agent", fake_agent)
    row = attack_kpi.attack_case(None, case, ticket, ws, runs)
    assert row["detected"] and row["kinds"] == ["under_fix"] and row["rounds"] == ["boundary", "types", "callers"]
    assert "context" in inputs[0] and case["probes"][0] != inputs[0].get("probes")  # 점검 입력은 주지 않는다

    findings.clear()  # 공격이 아무것도 못 찾으면 놓침
    assert not attack_kpi.attack_case(None, case, ticket, ws, runs)["detected"]


def test_kpi_row_uses_attack_result():
    attack = {"n": 10, "detected": 9, "by_flaw": {"under_fix": {"n": 6, "detected": 6},
                                                  "regression": {"n": 4, "detected": 3}}}
    row = next(r for r in ablation.kpi_rows({}, None, [], attack) if r["name"] == "공격 에이전트 탐지율")
    assert row["ok"] is True and row["value"].startswith("9/10 (90%") and "회귀 3/4" in row["value"]
    low = {**attack, "detected": 7}
    assert next(r for r in ablation.kpi_rows({}, None, [], low) if r["name"] == "공격 에이전트 탐지율")["ok"] is False
