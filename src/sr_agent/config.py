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
LLM_BUGS_RAW_PATH = MUTANTS_DIR / "llm_bugs_raw.jsonl"  # F2-1 LLM 결함 제안 원본 (함수 단위, 이어서 실행용)
TICKET_DRAFTS_PATH = TICKETS_DIR / "drafts.jsonl"  # F2-3 LLM 응답 (결함 단위, 이어서 실행용)
TICKETS_CSV_PATH = TICKETS_DIR / "tickets.csv"  # F2-4 분할까지 반영한 티켓 데이터셋
SPLIT_PATH = TICKETS_DIR / "split.json"
EVAL_RESULTS_DIR = PROJECT_ROOT / "eval" / "results"
REPLAY_DIR = DATA_DIR / "replay"  # 시연용 사전 실행 결과 (PRD 9장)
REPLAY_EVENT_DELAY_S = 0.04  # 재생 모드에서 진행 메시지 사이 간격

# 재현성 (분할, Hypothesis, 학습)
SEED = 42

# 코드 실행 (subprocess)
SUBPROCESS_TIMEOUT_S = 10
COVERAGE_TIMEOUT_S = 60  # get_coverage: 전체 테스트를 테스트별 기록으로 측정 (약 7초)
MAX_COVERAGE_TESTS = 20
JUDGE_TEST_TIMEOUT_S = 60  # 판정 노드의 전체 테스트 실행 (약 3초, 여유 있게)
MAX_ANALYSIS_TRIES = 3  # 재현 테스트 검증(원본 실패, 정답 구현 통과)에 실패하면 분석 재시도

# 공격 (F5-3, F4-3)
PROPERTY_TIMEOUT_S = 60  # property_attack·판정의 Hypothesis 실행
PROPERTY_MAX_EXAMPLES = 500
PROPERTY_CASES_REPORTED = 5  # 에이전트에 돌려줄 불일치 사례 수 (최소 사례는 따로)
JUDGE_PROPERTY_EXAMPLES = 200  # PRD 7.2: Hypothesis 입력 200개
ATTACK_COLLECTION = "attacks"  # 공격 이력 (Chroma)
ATTACK_MEMORY_TOP_K = 5
ATTACK_MAX_TURNS = 16  # 공격 라운드 하나의 턴(LLM 호출) 상한. 12에서는 S1 라운드 2개가 회귀 확인 전에 상한에 걸림
ATTACK_ROUNDS = 3  # 수정 시도 1회당 공격 라운드 수 (라운드마다 다른 관점)
ATTACK_ROUNDS_WITH_HISTORY = 5  # 이 티켓의 이전 시도가 판정에서 실패했을 때 (재시도)
MAX_FAILED_ATTACK_INPUTS = 5  # 판정이 걸러 낸 공격 입력 중 재시도 입력에 넘길 개수
ATTACK_PARALLEL = 3  # 동시에 실행하는 라운드 수. 한 묶음에서 확인된 발견이 나오면 다음 묶음은 건너뛴다
ATTACK_ANGLES = (  # 라운드 순서대로 하나씩. (이름, 화면 표시, 공격 에이전트 지시)
    ("boundary", "경계값", "근거 규칙이 정한 기준값의 바로 아래·같은 값·바로 위, 0·최댓값 근처를 공격한다."),
    ("types", "타입·빈 값", "None, 빈 문자열·빈 리스트, 음수, 아주 큰 수, 따옴표·공백·특수문자가 든 문자열처럼 "
     "함수 계약의 가장자리 값을 공격한다."),
    ("callers", "호출부 경로", "수정한 함수를 직접 부르지 말고 get_callers로 찾은 상위 함수를 통해 실제 업무 흐름의 "
     "입력으로 공격한다."),
    ("combination", "조합·순서", "여러 항목을 섞거나 순서를 바꾸거나 같은 값을 반복한 입력으로 공격한다 "
     "(예: 여러 쿠폰의 순서, 동명이인, 같은 날짜 반복)."),
    ("property", "성질 기반", "입력을 조금 바꿨을 때 결과가 따라야 할 성질(단조성, 대칭, 멱등, 합계 보존 등)을 정하고 "
     "property_attack으로 넓게 확인한다."),
)
COPY_IGNORE_PATTERNS = ("__pycache__", "*.pyc", ".pytest_cache", ".hypothesis", ".coverage", ".ruff_cache")
SECRET_ENV_MARKERS = ("API_KEY", "SECRET", "TOKEN")  # 자식 프로세스에 넘기지 않는 환경 변수

# 도구 출력 요약 (에이전트 컨텍스트 절약)
MAX_FAILURES_REPORTED = 10
MAX_TRACE_CHARS = 2000
MAX_DIFF_INPUTS = 50
MAX_VALUE_CHARS = 500

# 결함 주입 (F2-1, F2-2)
MUTANT_CAPS = {"ast": 4, "llm": 2}  # 주입 방식별 함수당 채택 상한 (합계 6, 특정 함수 쏠림 방지)
LLM_BUGS_PER_FUNCTION = 4  # 함수당 요청할 결함 수 (테스트로 걸러지는 몫 포함)
LLMINJECT_REASONING_EFFORT = "low"
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
EMBED_MODEL = "bge-m3"  # 2일차 비교로 결정 (UniXcoder 평가 Top-3 0.066)
RANKER_PARAMS = {  # 고정값. 아래 그리드 값은 학습 분할 교차검증으로 고른다
    "objective": "rank:ndcg",
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "tree_method": "hist",
}
RANKER_GRID = {
    "max_depth": [2, 4],
    "n_estimators": [150, 400],
    "learning_rate": [0.05],
    "min_child_weight": [1, 10],
}
CV_FOLDS = 5
# past_defects는 주 모델에서 뺀다 (처음 보는 함수에서 Top-3 0.775 → 0.295로 무너짐). 비교용(Ablation)으로만 보고
RANKER_EXCLUDED_FEATURES = ("past_defects",)
FAULT_MODEL_PATH = MODELS_DIR / "fault_ranker.json"
FAULT_MODEL_META_PATH = MODELS_DIR / "fault_ranker_meta.json"
FAULT_RESULTS_NAME = "fault_localization"  # eval/results/<이름>.{md,csv,json}
KPI_TOP3 = 0.80
PREDICT_MAX_TOP_K = 10
PREDICT_REASONS = 3  # SHAP 근거 개수 (F3-2)
MAX_TICKET_CHARS = 4000  # 입력 가드: 티켓 본문 길이 상한 (PRD 7.5)

# RAG (F4-1, F4-2)
HISTORY_COLLECTION = "history"  # 장애·조치 이력 (학습 분할 결함만)
RULES_COLLECTION = "rules"  # 사내 규칙 40건
RAG_MANIFEST_PATH = CHROMA_DIR / "manifest.json"  # 색인 시점의 입력 해시·건수
RULE_KEYWORD_BONUS = 0.3  # 규칙의 코드 패턴이 입력 코드에 있으면 유사도에 더하는 값
SEARCH_MAX_TOP_K = 10
MAX_CODE_CHARS = 8000  # search_rules 입력 코드 길이 상한
HISTORY_TEXT_CHARS = 400  # 검색 결과의 티켓 문장 길이 상한

# LLM (에이전트 단계에서 사용)
MODEL = "gpt-5"  # 분석·수정·공격 에이전트
MODEL_BULK = "gpt-6-luna"  # F2-3 티켓 문장 대량 생성 전용
LLM_TIMEOUT_S = 120
LLM_MAX_RETRIES = 3
MAX_OUTPUT_TOKENS = 16000  # 추론 토큰 포함
MAX_PATCH_RETRIES = 3
PRICE_PER_MTOK = {"input": 1.25, "cached": 0.125, "output": 10.0}  # gpt-5 가격 가정 (비용 추정용, USD)

# 에이전트 공통 (F5, F7-1)
MODEL_FALLBACK: str | None = None  # 기본 모델 호출이 서버 오류로 실패할 때 쓸 모델. 사용자 결정 전까지 끔
AGENT_REASONING_EFFORT = {"analysis": "high", "fix": "high", "attack": "medium"}
AGENT_MAX_TURNS = 20  # 에이전트 루프 반복(LLM 호출) 상한
# 티켓당 토큰 상한 (모든 에이전트 합계). S1 실측: 분석 13만, 수정 2만, 공격 라운드당 약 9만
# → 공격 3라운드면 약 42만, 재시도 1회 + 5라운드까지 약 90만
TICKET_TOKEN_LIMIT = 1_500_000
TOOL_OUTPUT_CHARS = 12000  # 도구 결과를 모델에 돌려줄 때 길이 상한
LOG_SUMMARY_CHARS = 300  # 실행 로그의 입력·출력 요약 길이
SEARCH_RULES_TOP_K = {"defect": 5, "standardize": 8, "security": 5}  # 수정 유형별 규칙 검색 개수
