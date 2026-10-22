"""공격 에이전트 탐지율 측정 (PRD 2장 KPI). 일부러 불완전하게 만든 패치 10건에 공격 단계만 실행한다.

실행 (프로젝트 루트, PYTHONPATH=src):
  python -m eval.run_attack_kpi --verify-only   # 패치가 정말 불완전한지만 확인 (LLM 호출 없음)
  python -m eval.run_attack_kpi                 # 확인 + 공격 (OpenAI API)
결과: eval/results/attack_kpi.{md,json}, 작업본: runs/attack_kpi/
"""

import argparse
import json

from sr_agent import config
from sr_agent.attack_kpi import run


def main() -> None:
    parser = argparse.ArgumentParser(description="공격 에이전트 탐지율 (불완전 패치 10건)")
    parser.add_argument("--verify-only", action="store_true", help="불완전 확인만 하고 공격은 하지 않음")
    args = parser.parse_args()
    client = None
    if not args.verify_only:
        from dotenv import load_dotenv

        from sr_agent import llm

        load_dotenv(config.PROJECT_ROOT / ".env")
        client = llm.get_client()
    print(json.dumps(run(client, verify_only=args.verify_only), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
