# CLAUDE.md

변경 요청(SR) 처리 에이전트. 티켓을 받아 결함 위치를 예측하고, 이력·규칙을 근거로 코드를 수정한 뒤, 공격 에이전트가 수정 미달과 회귀를 찾고, 판정 노드(코드)와 사람 승인을 거쳐 반영한다.

- 요구사항의 기준은 `PRD.md`다. 기능 ID(F1-1 등), 도구 명세, 판정 조건은 PRD를 따른다.
- PRD와 이 문서가 충돌하면 작업을 멈추고 사용자에게 묻는다.

## 기술 스택

| 영역 | 사용 |
|---|---|
| 언어 | Python 3.12 (에이전트·모델·화면·대상 프로젝트 실행) |
| LLM | OpenAI `gpt-5`(에이전트), `gpt-6-luna`(티켓 문장 대량 생성), OpenAI Python SDK (`openai`) Responses API + function calling |
| ML/DL | XGBoost, scikit-learn, sentence-transformers, SHAP |
| 테스트·공격 | pytest, coverage.py, Hypothesis |
| 결함 주입·검사 | AST 뮤테이션 스크립트(직접 작성, mutmut은 Windows 미지원으로 제외), ruff, bandit |
| 대상 프로젝트 실행 | pytz(원본 레거시 코드용), tzdata(Windows에서 `zoneinfo` 시간대 DB) |
| 벡터 저장소 | Chroma (로컬 영구 저장) |
| DB·UI | SQLite, Streamlit |

의존성은 `requirements.txt`에 버전을 고정해 관리한다. 새 패키지를 추가할 때는 사용자에게 먼저 묻는다.

## 폴더 구조

```
project/
├── PRD.md / CLAUDE.md
├── requirements.txt
├── docs/                    # 단계별 정리 문서 (step1.md 등)
├── target/                  # 가상 레거시 대상 프로젝트 (수정 대상, 아래 규칙 참고)
│   └── order_settlement/
│       ├── src/order_settlement/   # 주문·할인·결제·정산·고객 모듈
│       └── tests/
├── data/
│   ├── tickets/             # 결함 티켓 CSV, 분할 정보
│   ├── rules/rules.json     # 사내 규칙 40건
│   └── chroma/              # 벡터 저장소 (git 제외)
├── src/sr_agent/
│   ├── tools/               # 도구 함수 (PRD 7.3)
│   ├── agents/              # 분석·수정·공격 에이전트, prompts/
│   ├── workflow/            # 그래프, 상태, 판정 노드
│   ├── ml/                  # 피처, 학습, 예측, SHAP
│   ├── rag/                 # 색인·검색
│   └── datagen/             # 결함 주입, 티켓 생성
├── app/streamlit_app.py
├── eval/                    # E2E 배치, Ablation
├── tests/                   # 에이전트 시스템 자체의 테스트
├── runs/                    # 실행 로그 JSONL, 티켓 작업본 (git 제외)
│   └── <ticket_id>/
│       ├── original/        # 티켓 시작 시 복사한 원본 (수정 금지, version="original" 실행 위치)
│       ├── work/            # 작업본 (apply_patch 대상, version="patched" 실행 위치)
│       ├── repro_tests/     # 재현 테스트 (original·work 양쪽에 대해 실행)
│       └── state.json       # 티켓 상태
└── _build/                  # 기획서 PPT 생성 스크립트 (개발 대상 아님, 건드리지 않음)
```

## 명령어 (Windows PowerShell)

```powershell
uv venv .venv --python 3.12
.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt

pytest tests                                   # 에이전트 시스템 테스트
pytest target/order_settlement/tests           # 대상 프로젝트 테스트 (원본은 항상 통과해야 함)
ruff check src app eval tests
streamlit run app/streamlit_app.py
python -m eval.run_e2e --split holdout         # E2E 배치
```

API 키는 프로젝트 루트 `.env`의 `OPENAI_API_KEY`에 둔다. 진입점에서 `load_dotenv()`로 환경 변수에 올린 뒤 SDK가 환경 변수에서 읽게 한다. `.env`는 git에서 제외하고, 코드·다른 설정 파일·로그에 키를 쓰지 않는다.

## 가장 중요한 규칙

1. **`target/`의 레거시 스타일은 의도된 것이다.** `utcnow`, `pytz`, f-string SQL, 개인정보 로그 출력, 심어 둔 결함은 시연 재료다. 사용자가 명시적으로 요청하지 않는 한 `target/` 원본을 고치거나 현대화하지 않는다.
2. **원본은 읽기 전용.** 에이전트와 도구는 `target/`에서 테스트를 직접 돌리지 않는다. 사용자는 확인용으로 `target/`에서 pytest를 직접 돌릴 수 있으므로, `target/`에 `__pycache__`·`.pytest_cache` 같은 캐시가 있을 수 있다고 가정한다. 복사·해시 비교에서는 캐시를 제외한다 (`config.COPY_IGNORE_PATTERNS`). 티켓 시작 시 `runs/<ticket_id>/original/`과 `work/` 두 벌로 복사하고, 에이전트의 수정은 `work/`에서만 한다. E2E 티켓은 결함 주입 버전을 같은 방식으로 복사한다.
3. **정답을 건드리지 않는다.** 에이전트와 도구는 기존 테스트, 기대 출력, 판정 기준을 수정할 수 없다. 재현 테스트는 `runs/<ticket_id>/repro_tests/`에 추가만 한다.
4. **판정은 코드가 한다.** 판정 노드(`workflow/judge.py`)에 LLM 호출을 넣지 않는다.
5. **데이터는 공개·가상만.** 실제 사내 코드나 문서를 데이터·프롬프트에 넣지 않는다.
6. **누수 금지.** 평가 분할과 E2E 홀드아웃 20건은 KB, 프롬프트 예시, "과거 결함 수" 피처 계산에서 제외한다. 분할은 결함(`mutant_id`) 단위로 하고, 같은 결함에서 나온 티켓은 같은 분할에 둔다. 시연 시나리오 대상 함수의 결함은 학습 분할에만 둔다.

## 개발 방식

- 가이드 원칙: **기존 기능이 동작하는 상태를 유지하면서 한 번에 기능 하나만 추가한다.** 순서는 PRD 10장 일정을 따른다.
- 기능 하나를 끝낼 때마다 `pytest tests`와 대상 프로젝트 원본 테스트가 모두 통과하는지 확인한다.
- 기능을 완료하면 해당 PRD 기능 ID를 커밋 메시지나 작업 보고에 적는다.
- 시연 시나리오 S1 → S2 → S3 순서로 끝까지 동작시키는 것을 우선한다. Nice 기능은 S1~S3가 화면에서 동작한 뒤에만 시작한다.

## 코딩 규칙

- 타입 힌트를 쓰고, 공개 함수에는 짧은 docstring을 단다. 주석·docstring은 한국어로 써도 된다.
- 포맷과 린트는 ruff를 따른다.
- 설정값(모델 ID, 타임아웃, 재시도 횟수, 토큰 상한, 경로)은 `src/sr_agent/config.py` 한 곳에 둔다. 코드에 흩어 놓지 않는다.
- 난수를 쓰는 곳(분할, Hypothesis, 학습)은 `config.SEED`를 쓴다.

### 도구 함수

- 도구는 `src/sr_agent/tools/`에 순수 Python 함수로 만들고, 입력·출력은 PRD 7.3 명세를 그대로 따른다.
- 반환값은 JSON 직렬화 가능한 dict/list로 한다. 실패는 예외를 던지지 말고 `{"error": "..."}` 형태로 돌려준다.
- 출력은 에이전트 컨텍스트를 위해 요약한다. 긴 스택트레이스나 대량 입력 결과는 잘라서 개수와 대표 사례만 넘긴다.
- 코드 실행은 `subprocess`로 하고 타임아웃(기본 10초)을 반드시 건다. 작업 디렉터리는 작업본으로 고정하고, 작업본 밖 경로는 거부한다.
- `apply_patch`는 대상 메서드와, 같은 파일의 import 추가·삭제(`imports` 인자)만 바꿀 수 있다. 다른 파일, import 외 모듈 최상단 코드, 테스트 파일은 수정할 수 없다. 다른 함수가 아직 쓰는 import 삭제는 AST로 검사해 거부한다.
- 에이전트별로 쓸 수 있는 도구 목록을 따로 정의한다 (PRD 7.3의 "사용 에이전트" 열).

### 에이전트와 워크플로

- 워크플로는 Python으로 직접 짠 고정 그래프다 (분석 → 수정 → 공격 → 판정 → 승인). LangGraph 같은 프레임워크는 쓰지 않는다.
- 노드 사이에는 구조화된 결과(JSON, pydantic 모델)만 넘기고, 대화 이력은 넘기지 않는다.
- 티켓 상태는 `runs/<ticket_id>/state.json`에 저장해 중단 후 이어서 실행할 수 있게 한다.
- 수정 재시도는 최대 3회. 초과하면 상태를 "사람 검토 필요"로 바꾸고 멈춘다.
- 모든 LLM 호출과 도구 호출은 `runs/<run_id>.jsonl`에 기록한다 (단계, 도구, 입력 요약, 출력 요약, 토큰, 소요 시간).

### OpenAI API 사용 규칙

- 모델은 `config.MODEL = "gpt-5"`(분석·수정·공격 에이전트), `config.MODEL_BULK = "gpt-6-luna"`(F2-3 티켓 문장 생성 전용). 사용자가 바꾸라고 하기 전에는 다른 모델로 바꾸지 않는다. 3일차 S1 첫 실행 때 두 모델을 한 번 비교해 보고한다.
- 클라이언트는 인자 없이 `openai.OpenAI()`로 만든다 (`load_dotenv()` 후 환경 변수에서 키를 읽음). 타임아웃·재시도(`timeout`, `max_retries`)는 `config`에서 가져온다.
- Responses API(`client.responses.create`)를 기준으로 쓴다.
- 에이전트 루프는 직접 짠다 (OpenAI Agents SDK·LangChain 등 프레임워크 사용 안 함). 응답의 `function_call` 항목마다 도구를 실행하고 `function_call_output`(같은 `call_id`)으로 돌려준다. 루프 반복 횟수 상한은 `config`에 둔다. 실행 전 승인이나 로깅이 필요하면 도구 함수 안에서 처리한다.
- 도구 정의에는 `strict: True`와 `additionalProperties: False`를 붙인다. `tool_choice`는 `auto`를 기본으로 하고, 사용할 도구는 프롬프트에서 지시한다.
- 도구 인자(`arguments`)는 문자열 비교하지 말고 `json.loads`로 파싱한 dict로 다룬다.
- 구조화된 결과가 필요하면 structured outputs(`client.responses.parse(..., text_format=<pydantic 모델>)`)를 쓰고 `output_parsed`를 사용한다.
- 추론 모델이면 `reasoning={"effort": ...}`를 항상 명시한다. 분석·수정 에이전트는 `high`, 공격 입력 설계는 `medium`부터 시작해 측정 후 조정한다. 모델이 지원하지 않는 파라미터(예: 추론 모델의 `temperature`)는 보내지 않는다.
- 응답을 쓰기 전에 `status`를 확인한다. `incomplete`면 `incomplete_details`를 로그에 남기고, 거부(refusal) 응답이면 해당 티켓을 사람 검토로 넘긴다.
- `max_output_tokens`는 추론 토큰까지 고려해 넉넉히(16000 내외) 잡는다.
- prompt caching은 앞부분이 같은 요청에 자동 적용된다. 시스템 지시(`instructions`)와 도구 정의는 고정 텍스트로 요청 앞쪽에 두고, 현재 시각·실행 ID처럼 매번 바뀌는 값을 넣지 않는다.
- 응답마다 `usage`(입력·출력·캐시·추론 토큰)를 실행 로그에 기록하고, 티켓당 토큰 상한을 넘으면 멈춘다.
- SDK 사용법이 확실하지 않으면 추측하지 말고 공식 SDK 문서를 확인한다.

### ML 코드

- 학습 스크립트는 입력 데이터 경로와 시드를 받아 같은 결과를 재현해야 한다.
- 모델 파일은 `data/models/`에 저장하고, 비교 실험 결과(세 모델 지표)는 `eval/results/`에 표로 남긴다.
- 평가 지표는 스택트레이스 유무별·주입 방식별로 나눠 함께 기록한다.

## 테스트

- 도구 함수마다 단위 테스트를 둔다. LLM 호출이 없는 부분(도구, 판정 노드, 피처, 분할)은 반드시 테스트한다.
- LLM을 부르는 테스트는 기본 실행에서 제외하고(`@pytest.mark.llm`), 필요할 때만 `pytest -m llm`으로 돌린다. API 비용이 들기 때문이다.
- 판정 노드는 "통과해야 하는 패치"와 "통과하면 안 되는 패치"를 둘 다 테스트한다.

## 하지 말 것

- `target/` 원본 수정, 기존 테스트·기대 출력 수정
- 판정 노드에 LLM 판단 추가
- 실제 사내 데이터 사용, `.env` 밖(코드·로그·커밋)에 API 키 저장
- 사용자 확인 없이 새 의존성 추가, Nice 기능 선행 개발
- `_build/` 폴더 수정 (기획서 PPT 생성용)
