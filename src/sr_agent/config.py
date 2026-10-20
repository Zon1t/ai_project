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
MUTANTS_PATH = MUTANTS_DIR / "mutants.jsonl"
TICKET_DRAFTS_PATH = TICKETS_DIR / "drafts.jsonl"  # F2-3 LLM 응답 (결함 단위, 이어서 실행용)
TICKETS_CSV_PATH = TICKETS_DIR / "tickets.csv"  # F2-4 분할까지 반영한 티켓 데이터셋
SPLIT_PATH = TICKETS_DIR / "split.json"
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

# 티켓 생성·분할 (F2-3, F2-4)
TICKETGEN_REASONING_EFFORT = "low"
TICKETGEN_WORKERS = 4
TICKETGEN_MAX_ATTEMPTS = 2  # 본문 검사(정답 함수명·식별자) 실패 시 1회 재시도
TICKET_FAILURES_IN_PROMPT = 3  # 프롬프트에 넣을 실패 정보 수
E2E_HOLDOUT_SIZE = 20  # 결함 단위
SPLIT_TEST_RATIO = 0.2  # 홀드아웃·시연 함수를 뺀 나머지 결함의 평가 분할 비율
DEMO_METHODS = ("discount.apply_coupon", "settlement.calc_due_date", "customer_dao.find_by_name")  # 학습 분할에만

# 결함 위치 예측 (F3)
CACHE_DIR = DATA_DIR / "cache"  # 임베딩 캐시 (git 제외, 다시 계산 가능)
EMBED_MODELS = {"bge-m3": "BAAI/bge-m3", "unixcoder": "microsoft/unixcoder-base"}  # 2일차 비교 대상
EMBED_MAX_SEQ_LEN = 512
EMBED_BATCH_SIZE = 16
TFIDF_NGRAM_RANGE = (2, 4)  # 문자 n-gram (한국어 형태소 분석기 없이)
RANKER_PARAMS = {
    "objective": "rank:ndcg",
    "n_estimators": 300,
    "learning_rate": 0.05,
    "max_depth": 4,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "tree_method": "hist",
}
FAULT_MODEL_PATH = MODELS_DIR / "fault_ranker.json"
FAULT_MODEL_META_PATH = MODELS_DIR / "fault_ranker_meta.json"
FAULT_RESULTS_NAME = "fault_localization"  # eval/results/<이름>.{md,csv,json}
KPI_TOP3 = 0.80

# LLM (에이전트 단계에서 사용)
MODEL = "gpt-5"  # 분석·수정·공격 에이전트
MODEL_BULK = "gpt-6-luna"  # F2-3 티켓 문장 대량 생성 전용
LLM_TIMEOUT_S = 120
LLM_MAX_RETRIES = 3
MAX_OUTPUT_TOKENS = 16000  # 추론 토큰 포함
MAX_PATCH_RETRIES = 3
