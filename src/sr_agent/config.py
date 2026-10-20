"""SR 에이전트 설정. 경로·타임아웃·시드·모델 등 설정값은 이 파일에만 둔다."""

from pathlib import Path

# 경로
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"

# 대상 프로젝트 (가상 레거시)
TARGET_ROOT = PROJECT_ROOT / "target" / "order_settlement"
TARGET_PACKAGE = "order_settlement"
TARGET_SRC_SUBDIR = "src"  # 대상 프로젝트 루트 기준
TARGET_TEST_SUBDIR = "tests"
TARGET_CONFIG_FILE = "pyproject.toml"

# 티켓 실행 디렉터리: runs/<ticket_id>/{original,work,repro_tests,state.json}
RUNS_DIR = PROJECT_ROOT / "runs"
ORIGINAL_DIRNAME = "original"
WORK_DIRNAME = "work"
REPRO_DIRNAME = "repro_tests"
REPORTS_DIRNAME = ".reports"
STATE_FILENAME = "state.json"
TICKET_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"

# 데이터·결과
DATA_DIR = PROJECT_ROOT / "data"
TICKETS_DIR = DATA_DIR / "tickets"
RULES_PATH = DATA_DIR / "rules" / "rules.json"
CHROMA_DIR = DATA_DIR / "chroma"
MODELS_DIR = DATA_DIR / "models"
MUTANTS_DIR = DATA_DIR / "mutants"  # F2-1·F2-2 결함 버전 (mutants.jsonl, summary.json)
EVAL_RESULTS_DIR = PROJECT_ROOT / "eval" / "results"

# 재현성 (분할, Hypothesis, 학습)
SEED = 42

# 코드 실행 (subprocess)
SUBPROCESS_TIMEOUT_S = 10
COPY_IGNORE_PATTERNS = ("__pycache__", "*.pyc", ".pytest_cache", ".hypothesis", ".coverage", ".ruff_cache")
SECRET_ENV_MARKERS = ("API_KEY", "SECRET", "TOKEN")  # 자식 프로세스에 넘기지 않는 환경 변수

# 도구 출력 요약 (에이전트 컨텍스트 절약)
MAX_FAILURES_REPORTED = 10
MAX_TRACE_CHARS = 2000
MAX_DIFF_INPUTS = 50
MAX_VALUE_CHARS = 500

# 결함 주입 (F2-1, F2-2)
MUTANTS_PER_METHOD = 5  # 함수당 채택 상한 (특정 함수 쏠림 방지)
MUTGEN_WORKERS = 4  # 뮤턴트 테스트 병렬 작업본 수
MUTANT_TRACES_KEPT = 3  # 뮤턴트마다 저장할 실패 트레이스 수

# LLM (에이전트 단계에서 사용)
MODEL = "gpt-5"  # 분석·수정·공격 에이전트
MODEL_BULK = "gpt-6-luna"  # F2-3 티켓 문장 대량 생성 전용
MAX_PATCH_RETRIES = 3
