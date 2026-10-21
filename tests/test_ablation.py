"""Ablation 비교표·KPI 표 (F7-3, PRD 2장). E2E 결과 파일은 가짜로 만들고, 시연은 실제 재생 데이터를 쓴다."""

import json

from sr_agent import config
from sr_agent.ablation import CONDITIONS, demo_results, kpi_rows, load_fault_top3, render_report, write_report


def _summary(solved, leaks, tokens, seconds, citation=0.9, hidden=None):
    return {"n": 20, "solved": solved, "solved_rate": solved / 20, "hidden_pass": hidden if hidden is not None
            else solved - leaks, "regression_leaks": leaks, "model_top3_rate": 0.85, "agent_location_rate": 1.0,
            "mean_attempts": 1.1, "citation_rate": citation, "tokens_mean": tokens, "cost_usd_total": 9.5,
            "seconds_mean": seconds}


def _rows(solved_ids):
    return [{"ticket_id": f"E2E-{i}-A", "method": f"m.f{i}", "solved": i in solved_ids, "attempts": 1,
             "regression_leak": False} for i in range(3)]


RESULTS = {
    "full": {"summary": _summary(17, 0, 300_000, 360.0), "rows": _rows({0, 1})},
    "no_predict": {"summary": _summary(15, 0, 340_000, 420.0), "rows": _rows({0})},
    "no_attack": {"summary": _summary(16, 2, 200_000, 250.0), "rows": _rows({0, 1, 2})},
}
DEMOS = [{"ticket_id": "SR-2041", "scenario": "S1", "type": "defect", "passed": True, "attempts": 1,
          "seconds": 365.0, "cited": True, "cited_ids": ["BIZ-CPN-01"]},
         {"ticket_id": "SR-2077", "scenario": "S2", "type": "standardize", "passed": True, "attempts": 2,
          "seconds": 401.0, "cited": True, "cited_ids": ["DEP-DT-01"]}]


def test_kpi_rows_follow_prd_and_judge_targets():
    rows = {r["name"]: r for r in kpi_rows(RESULTS, 0.803, DEMOS)}
    assert list(rows) == ["결함 위치 Top-3 정확도", "SR당 처리 시간", "공격 에이전트 탐지율", "근거 인용률",
                          "E2E 해결률", "예측 도구 기여", "회귀 누출", "자동 검증 통과"]  # PRD 2장 순서
    assert rows["결함 위치 Top-3 정확도"]["ok"] is True and rows["결함 위치 Top-3 정확도"]["value"].startswith("0.803")
    assert rows["SR당 처리 시간"]["ok"] is True and "S2 6.7분" in rows["SR당 처리 시간"]["value"]
    assert rows["공격 에이전트 탐지율"]["ok"] is None
    assert rows["E2E 해결률"]["ok"] is True and "17/20 (85%)" in rows["E2E 해결률"]["value"]
    assert rows["예측 도구 기여"]["ok"] is True and "+10%p" in rows["예측 도구 기여"]["value"]
    assert "-40,000" in rows["예측 도구 기여"]["value"] and "-60초" in rows["예측 도구 기여"]["value"]
    assert rows["회귀 누출"]["ok"] is True and "공격 끔 2건" in rows["회귀 누출"]["value"]
    assert rows["자동 검증 통과"]["ok"] is False  # 재생 데이터 2건뿐 (3유형 중 2)
    assert "S2 2회째 통과" in rows["자동 검증 통과"]["value"]


def test_missing_conditions_are_marked_unmeasured():
    rows = {r["name"]: r for r in kpi_rows({"full": None}, None, [])}
    assert rows["E2E 해결률"]["ok"] is None and rows["예측 도구 기여"]["ok"] is None
    md = render_report({"full": RESULTS["full"]}, None, [])
    assert "미실행" in md and "⏳ 미측정" in md


def test_render_report_compares_conditions():
    md = render_report(RESULTS, 0.803, DEMOS)
    assert md.index("## KPI") < md.index("## Ablation")
    header = "| 지표 | " + " | ".join(label for label, _ in CONDITIONS.values()) + " |"
    assert header in md
    assert "| **해결률** (사람 개입 없이 판정 통과) | 17/20 (85%) | 15/20 (75%) | 16/20 (80%) |" in md
    assert "| 회귀 누출 (판정 통과 후 숨긴 테스트 실패) | 0건 | 0건 | 2건 |" in md
    assert "| E2E-2-A | `m.f2` | ❌ 1회 | ❌ 1회 | ✅ 1회 |" in md


def test_write_report_reads_result_files(tmp_path):
    for tag, data in RESULTS.items():
        (tmp_path / f"e2e_{tag}.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / f"{config.FAULT_RESULTS_NAME}.json").write_text(json.dumps({"results": {
        "③ 혼합형 (XGBoost Ranker)": [{"group": "전체", "top3": 0.803}],
        "③-A 혼합형 + 과거 결함 수 (비교용)": [{"group": "전체", "top3": 0.845}]}}), encoding="utf-8")
    assert load_fault_top3(tmp_path) == 0.803  # 과거 결함 수 비교 모델이 아니라 주 모델
    path = write_report(tmp_path)
    data = json.loads((tmp_path / "kpi_ablation.json").read_text(encoding="utf-8"))
    assert path.name == "kpi_ablation.md" and len(data["kpi"]) == 8
    assert set(data["conditions"]) == set(CONDITIONS)


def test_demo_results_from_replay_data():
    demos = demo_results()
    assert [d["ticket_id"] for d in demos] == ["SR-2041", "SR-2077", "SR-2103"]
    assert all(d["passed"] and d["seconds"] and d["seconds"] < 15 * 60 for d in demos)
    assert all(d["cited"] is not None and d["field_ids"] for d in demos)  # 세 건 모두 패치와 근거 필드가 있음
    s2 = next(d for d in demos if d["ticket_id"] == "SR-2077")
    assert "DEP-DT-01" in s2["cited_ids"]  # 본문 "DEP-DT-01에 따라"처럼 한글이 붙어도 ID 전체를 잡는다
