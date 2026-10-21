"""E2E 배치 실행 (PRD F7-2, F7-3).

실행 (프로젝트 루트, PYTHONPATH=src):
  python -m eval.run_e2e --split holdout                 # 홀드아웃 20건, 전체 기능
  python -m eval.run_e2e --split holdout --limit 2       # 2건만
  python -m eval.run_e2e --split holdout --no-predict    # Ablation: 예측 도구 끔 (태그 no_predict)
  python -m eval.run_e2e --split holdout --no-attack     # Ablation: 공격 단계 끔 (태그 no_attack)
  python -m eval.run_e2e --split holdout --attack-rounds 1   # 공격 라운드 수 고정 (태그 r1)
결과: eval/results/e2e_<태그>.{md,csv,json}, 작업본: runs/e2e/<태그>/
"""

import argparse
import json

from sr_agent import config
from sr_agent.e2e import run_e2e
from sr_agent.workflow.graph import RunOptions


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E 배치 (홀드아웃 결함 일괄 처리)")
    parser.add_argument("--split", choices=["holdout"], default="holdout")
    parser.add_argument("--limit", type=int, default=None, help="앞에서부터 N건만")
    parser.add_argument("--reporter", choices=["A", "B"], default="A", help="A: 운영 담당자 티켓, B: QA 티켓")
    parser.add_argument("--no-predict", action="store_true", help="분석 에이전트에 예측 도구를 주지 않음")
    parser.add_argument("--no-attack", action="store_true", help="공격 단계를 건너뜀")
    parser.add_argument("--attack-rounds", type=int, default=None,
                        help="공격 라운드 수 고정 (기본: 3, 재시도면 5)")
    parser.add_argument("--workers", type=int, default=1, help="동시에 처리할 티켓 수")
    parser.add_argument("--tag", default=None, help="결과 이름 (기본: 조건으로 정함)")
    parser.add_argument("--restart", action="store_true", help="끝난 티켓도 처음부터 다시")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(config.PROJECT_ROOT / ".env")
    options = RunOptions(use_predict=not args.no_predict, use_attack=not args.no_attack,
                         attack_rounds=args.attack_rounds)
    off = [n for n, flag in (("no_predict", args.no_predict), ("no_attack", args.no_attack)) if flag]
    if args.attack_rounds is not None and not args.no_attack:
        off.append(f"r{args.attack_rounds}")
    condition = args.tag or ("_".join(off) if off else "full")
    tag = condition + (f"_n{args.limit}" if args.limit else "")
    summary = run_e2e(tag, options, args.reporter, args.limit, args.workers, args.restart, runs_tag=condition)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"결과: {config.EVAL_RESULTS_DIR / f'e2e_{tag}.md'}")


if __name__ == "__main__":
    main()
