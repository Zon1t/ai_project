"""Ablation 비교표 (PRD F7-3)와 KPI 표 (PRD 2장).

같은 홀드아웃 20건을 세 조건으로 돌린 E2E 결과(eval/results/e2e_<조건>.json)를 모아 비교한다.
- full: 전체 기능 / no_predict: 분석 에이전트에 예측 도구 없음 / no_attack: 공격 단계 없음
KPI 표는 PRD 2장의 8개 지표를 같은 형식(지표 · 목표 · 측정 방법)으로 쓰고, 측정값과 달성 여부를 붙인다.
측정값 출처: E2E(full·Ablation), 결함 위치 모델 평가(fault_localization.json), 시연 재생 데이터(data/replay).
"""

import json
from datetime import datetime
from pathlib import Path

from sr_agent import config
from sr_agent.workflow.graph import RunOptions
from sr_agent.workflow.state import TicketState

CONDITIONS: dict[str, tuple[str, RunOptions]] = {
    "full": ("전체 기능", RunOptions()),
    "no_predict": ("예측 도구 끔", RunOptions(use_predict=False)),
    "no_attack": ("공격 단계 끔", RunOptions(use_attack=False)),
}
DEMO_TICKETS = ("SR-2041", "SR-2077", "SR-2103")
KPI_TARGET_MINUTES = 15
KPI_CITATION = 0.80
KPI_SOLVED = 0.60
KPI_LEAKS = 1


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v:.0%}"


def load_condition(tag: str, results_dir: Path | None = None) -> dict | None:
    path = (results_dir or config.EVAL_RESULTS_DIR) / f"e2e_{tag}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def load_fault_top3(results_dir: Path | None = None) -> float | None:
    """결함 위치 모델(혼합형)의 평가 분할 Top-3 (2일차 결과)."""
    path = (results_dir or config.EVAL_RESULTS_DIR) / f"{config.FAULT_RESULTS_NAME}.json"
    if not path.is_file():
        return None
    results = json.loads(path.read_text(encoding="utf-8"))["results"]
    key = next((k for k in results if "혼합형" in k and "과거" not in k), None)
    row = next((r for r in results.get(key, []) if r["group"] == "전체"), None) if key else None
    return row["top3"] if row else None


def demo_results(replay_dir: Path | None = None) -> list[dict]:
    """시연 재생 데이터(S1~S3)의 판정·처리 시간·근거 인용."""
    from sr_agent.e2e import cited_ids, field_ids

    out = []
    for ticket_id in DEMO_TICKETS:
        src = (replay_dir or config.REPLAY_DIR) / ticket_id
        state = TicketState.load(src / config.STATE_FILENAME)
        if state is None:
            continue
        events = [json.loads(line) for line in (src / "events.jsonl").read_text(encoding="utf-8").splitlines()
                  if line.strip()] if (src / "events.jsonl").is_file() else []
        seconds = ((datetime.fromisoformat(events[-1]["ts"]) - datetime.fromisoformat(events[0]["ts"])).total_seconds()
                   if len(events) > 1 else None)
        cited = cited_ids(state)
        out.append({"ticket_id": ticket_id, "scenario": state.ticket.scenario, "type": state.ticket.type,
                    "passed": state.passed, "attempts": len(state.attempts), "seconds": seconds,
                    "cited": bool(cited) if cited is not None else None, "cited_ids": cited or [],
                    "field_ids": field_ids(state) or []})
    return out


def _summary(data: dict | None) -> dict:
    return data["summary"] if data else {}


def ablation_table(results: dict[str, dict | None]) -> list[str]:
    """조건별 지표 비교표 (Markdown 줄)."""
    tags = list(CONDITIONS)
    s = {t: _summary(results.get(t)) for t in tags}

    def row(label, fn):
        return f"| {label} | " + " | ".join(fn(s[t]) if s[t] else "미실행" for t in tags) + " |"

    def frac(key):
        return lambda x: f"{x.get(key, 0)}/{x['n']} ({_pct(x.get(key, 0) / x['n'] if x['n'] else None)})"

    return [
        "| 지표 | " + " | ".join(CONDITIONS[t][0] for t in tags) + " |",
        "|---|" + "---|" * len(tags),
        row("**해결률** (사람 개입 없이 판정 통과)", frac("solved")),
        row("판정 통과 + 숨긴 테스트 통과", frac("hidden_pass")),
        row("회귀 누출 (판정 통과 후 숨긴 테스트 실패)", lambda x: f"{x.get('regression_leaks', 0)}건"),
        row("모델 Top-3 적중", lambda x: _pct(x.get("model_top3_rate"))),
        row("에이전트 최종 위치 정답", lambda x: _pct(x.get("agent_location_rate"))),
        row("평균 수정 시도", lambda x: str(x.get("mean_attempts"))),
        row("근거 인용률", lambda x: _pct(x.get("citation_rate"))),
        row("토큰 평균", lambda x: f"{x.get('tokens_mean', 0):,}"),
        row("비용 합계", lambda x: f"${x.get('cost_usd_total', 0)}"),
        row("평균 소요 시간", lambda x: f"{x.get('seconds_mean')}초"),
    ]


def _delta(a: dict, b: dict, key: str, fmt) -> str:
    if not a or not b or a.get(key) is None or b.get(key) is None:
        return "-"
    return fmt(a[key] - b[key])


def kpi_rows(results: dict[str, dict | None], fault_top3: float | None, demos: list[dict]) -> list[dict]:
    """PRD 2장 KPI 8개: 지표, 목표, 측정 방법, 결과, 달성(True/False/None=미측정)."""
    full, no_pred, no_att = (_summary(results.get(t)) for t in ("full", "no_predict", "no_attack"))
    rows = []

    rows.append({"name": "결함 위치 Top-3 정확도", "target": "0.80 이상",
                 "method": "평가 분할 티켓 524건(결함 단위 분할), 혼합형 XGBoost Ranker",
                 "value": "-" if fault_top3 is None else f"{fault_top3:.3f}"
                 + (f" (E2E 홀드아웃 모델 Top-3 {_pct(full.get('model_top3_rate'))}, "
                    f"에이전트 최종 위치 {_pct(full.get('agent_location_rate'))})" if full else ""),
                 "ok": None if fault_top3 is None else fault_top3 >= config.KPI_TOP3})

    timed = [d["seconds"] for d in demos if d["seconds"] is not None]
    worst = max(timed) if timed else None
    rows.append({"name": "SR당 처리 시간", "target": f"2시간 → {KPI_TARGET_MINUTES}분",
                 "method": "시연 티켓 S1~S3의 접수부터 승인 요청까지 (재생 데이터 실행 기록)",
                 "value": ("-" if worst is None else
                           ", ".join(f"{d['scenario']} {d['seconds'] / 60:.1f}분" for d in demos if d["seconds"])
                           + (f" (E2E 평균 {full['seconds_mean'] / 60:.1f}분)" if full.get("seconds_mean") else "")),
                 "ok": None if worst is None else worst <= KPI_TARGET_MINUTES * 60})

    rows.append({"name": "공격 에이전트 탐지율", "target": "80% 이상",
                 "method": "일부러 불완전하게 만든 패치 10건 중 미달·회귀 탐지 수",
                 "value": "미측정 (불완전 패치 10건 작성 예정)", "ok": None})

    demo_cited = [d["cited"] for d in demos if d["cited"] is not None]
    rate = full.get("citation_rate")
    rows.append({"name": "근거 인용률", "target": f"{KPI_CITATION:.0%} 이상",
                 "method": "마지막 패치 설명 본문에 규칙 ID·이력 ID가 포함된 비율 (E2E 전체 기능 + 시연 S1~S3)",
                 "value": (f"E2E {_pct(rate)} (필드 기준 {_pct(full.get('citation_field_rate'))}), "
                           f"시연 {sum(demo_cited)}/{len(demo_cited)}") if full else "-",
                 "ok": None if rate is None else rate >= KPI_CITATION and all(demo_cited)})

    solved = full.get("solved_rate")
    rows.append({"name": "E2E 해결률", "target": f"{KPI_SOLVED:.0%} 이상 (가정치)",
                 "method": "홀드아웃 20건 중 사람 개입 없이 판정 노드를 통과한 비율",
                 "value": (f"{full['solved']}/{full['n']} ({_pct(solved)}), 숨긴 테스트까지 통과 "
                           f"{full.get('hidden_pass', '-')}/{full['n']}") if full else "-",
                 "ok": None if solved is None else solved >= KPI_SOLVED})

    rows.append({"name": "예측 도구 기여", "target": "해결률 향상",
                 "method": "Ablation: predict_fault_location 유무별 해결률·토큰·소요 시간",
                 "value": ("-" if not (full and no_pred) else
                           f"해결률 {_pct(full['solved_rate'])} vs 끔 {_pct(no_pred['solved_rate'])} "
                           f"({_delta(full, no_pred, 'solved_rate', lambda d: f'{d * 100:+.0f}%p')}), "
                           f"토큰 평균 {_delta(full, no_pred, 'tokens_mean', lambda d: f'{d:+,}')}, "
                           f"시간 {_delta(full, no_pred, 'seconds_mean', lambda d: f'{d:+.0f}초')}"),
                 "ok": None if not (full and no_pred) else full["solved_rate"] > no_pred["solved_rate"]})

    rows.append({"name": "회귀 누출", "target": f"{KPI_LEAKS}건 이하",
                 "method": "Ablation: 공격 단계 유무별, 판정 통과 후 숨은 회귀 테스트에 걸린 패치 수",
                 "value": ("-" if not full else f"공격 사용 {full.get('regression_leaks', 0)}건"
                           + (f" vs 공격 끔 {no_att.get('regression_leaks', 0)}건" if no_att else "")),
                 "ok": None if not full else full.get("regression_leaks", 0) <= KPI_LEAKS})

    passed = [d for d in demos if d["passed"]]
    each = [f"{d['scenario']} {d['attempts']}회째 통과" if d["passed"] else f"{d['scenario']} 실패" for d in demos]
    rows.append({"name": "자동 검증 통과", "target": "3유형 모두",
                 "method": "시연 시나리오 3개(결함 수정·코드 표준화·보안 조치)가 판정 노드 통과",
                 "value": f"{len(passed)}/{len(DEMO_TICKETS)} ({', '.join(each)})",
                 "ok": len(passed) == len(DEMO_TICKETS)})
    return rows


def kpi_table(rows: list[dict]) -> list[str]:
    marks = {True: "✅ 달성", False: "❌ 미달", None: "⏳ 미측정"}
    return ["| 지표 | 목표 | 측정 방법 | 결과 | 달성 |", "|---|---|---|---|---|",
            *[f"| {r['name']} | {r['target']} | {r['method']} | {r['value']} | {marks[r['ok']]} |" for r in rows]]


def render_report(results: dict[str, dict | None], fault_top3: float | None, demos: list[dict]) -> str:
    """KPI 표 + Ablation 비교표 + 조건별 티켓 결과 (Markdown)."""
    price = config.PRICE_PER_MTOK
    lines = ["# KPI와 Ablation (PRD 2장, F7-3)", "",
             "- E2E: 홀드아웃 결함 20건(운영 담당자 티켓), 조건마다 공격 이력 분리, "
             "숨긴 테스트는 판정 통과 뒤 따로 실행",
             f"- 가격 가정(100만 토큰당): 입력 ${price['input']}, 캐시 ${price['cached']}, 출력 ${price['output']}", "",
             "## KPI (PRD 2장)", "", *kpi_table(kpi_rows(results, fault_top3, demos)), "",
             "## Ablation: 조건별 비교 (F7-3)", "", *ablation_table(results), ""]
    lines += ["## 조건별 티켓 결과", "",
              "| 티켓 | 정답 함수 | " + " | ".join(CONDITIONS[t][0] for t in CONDITIONS) + " |",
              "|---|---|" + "---|" * len(CONDITIONS)]
    by_tag = {t: {r["ticket_id"]: r for r in (results.get(t) or {}).get("rows", [])} for t in CONDITIONS}
    ids = list(dict.fromkeys(i for rows in by_tag.values() for i in rows))
    for tid in ids:
        method = next(rows[tid]["method"] for rows in by_tag.values() if tid in rows)
        cells = []
        for t in CONDITIONS:
            r = by_tag[t].get(tid)
            cells.append("-" if r is None else ("✅" if r["solved"] else "❌")
                         + (" 누출" if r.get("regression_leak") else "") + f" {r['attempts']}회")
        lines.append(f"| {tid} | `{method}` | " + " | ".join(cells) + " |")
    lines += ["", "## 시연 시나리오 (재생 데이터)", "",
              "| 티켓 | 유형 | 판정 | 시도 | 처리 시간 | 설명 본문 인용 | rule_ids·history_ids |",
              "|---|---|---|---|---|---|---|"]
    for d in demos:
        sec = "-" if d["seconds"] is None else f"{d['seconds']:.0f}초"
        lines.append(f"| {d['ticket_id']} ({d['scenario']}) | {d['type']} | {'통과' if d['passed'] else '실패'} | "
                     f"{d['attempts']} | {sec} | {', '.join(d['cited_ids']) or '없음'} | "
                     f"{', '.join(d.get('field_ids', [])) or '-'} |")
    return "\n".join(lines) + "\n"


def write_report(results_dir: Path | None = None, replay_dir: Path | None = None) -> Path:
    """eval/results/kpi_ablation.{md,json}을 쓴다."""
    out = results_dir or config.EVAL_RESULTS_DIR
    results = {t: load_condition(t, out) for t in CONDITIONS}
    fault_top3 = load_fault_top3(out)
    demos = demo_results(replay_dir)
    path = out / "kpi_ablation.md"
    path.write_text(render_report(results, fault_top3, demos), encoding="utf-8", newline="\n")
    data = {"kpi": kpi_rows(results, fault_top3, demos), "demos": demos,
            "conditions": {t: _summary(results[t]) for t in CONDITIONS}}
    (out / "kpi_ablation.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8", newline="\n")
    return path
