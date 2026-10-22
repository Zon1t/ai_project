"""Ablation 실행과 KPI 표 (PRD F7-3, 2장).

같은 홀드아웃 20건을 세 조건으로 실행하고 비교표를 쓴다 (프로젝트 루트, PYTHONPATH=src):
  python -m eval.run_ablation                          # full → no_predict → no_attack 차례로 실행 후 표 작성
  python -m eval.run_ablation --conditions no_attack   # 일부 조건만
  python -m eval.run_ablation --report-only            # 이미 있는 e2e_<조건>.json으로 표만 다시 씀
  python -m eval.run_ablation --suite v12 --workers 2  # v1.2 조건 (결과 e2e_v12_<조건>, kpi_ablation_v12)
조건 하나를 따로 돌리려면 python -m eval.run_e2e --no-predict 등을 써도 결과 파일 이름이 같다.
결과: eval/results/e2e_<조건>.{md,csv,json}, eval/results/kpi_ablation.{md,json}
"""

import argparse

from sr_agent import config
from sr_agent.ablation import CONDITIONS, SUITES, suite_conditions, write_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Ablation (예측 도구·공격 단계 유무) 실행과 KPI 표")
    parser.add_argument("--conditions", nargs="+", choices=list(CONDITIONS), default=list(CONDITIONS))
    parser.add_argument("--workers", type=int, default=1, help="조건마다 동시에 처리할 티켓 수")
    parser.add_argument("--restart", action="store_true", help="끝난 티켓도 처음부터 다시 (공격 이력도 비움)")
    parser.add_argument("--report-only", action="store_true", help="실행하지 않고 표만 다시 씀")
    parser.add_argument("--suite", choices=list(SUITES), default="legacy",
                        help="legacy: 5일차~v1.1 조건(기존 결과), v12: 재시도 공격 축소·코드 미리 넣기")
    args = parser.parse_args()

    if not args.report_only:
        from dotenv import load_dotenv

        from sr_agent.e2e import run_e2e

        load_dotenv(config.PROJECT_ROOT / ".env")
        conditions = suite_conditions(args.suite)
        for base in args.conditions:
            tag, label, options = conditions[base]
            print(f"=== {tag} ({label}) ===", flush=True)
            from sr_agent.llm import LLMQuotaExceeded

            try:
                summary = run_e2e(tag, options, workers=args.workers, restart=args.restart)
            except LLMQuotaExceeded as e:  # 끝난 티켓은 저장돼 있으니 같은 명령으로 이어서 실행
                raise SystemExit(f"중단: {e}") from None
            print(f"  해결 {summary['solved']}/{summary['n']}, 누출 {summary['regression_leaks']}건, "
                  f"${summary['cost_usd_total']}", flush=True)
    print(f"결과: {write_report(suite=args.suite)}")


if __name__ == "__main__":
    main()
