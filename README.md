# RedFix: 변경 요청(SR) 처리 에이전트

운영 시스템의 변경 요청(SR) 티켓을 받아 결함 위치를 좁히고, 사내 규칙·장애 이력을 근거로 코드를 고친 뒤, 공격 에이전트가 덜 고친 곳과 회귀를 찾고, 코드로 된 판정 노드와 사람의 승인을 거쳐 반영하는 에이전트 시스템입니다.

- 요구사항 기준: [PRD.md](PRD.md)
- 화면 사용법: [docs/user_guide.md](docs/user_guide.md)
- 단계별 개발 기록: [docs/step1.md](docs/step1.md) ~ [docs/step7.md](docs/step7.md)
- 실험 결과: [eval/results/](eval/results/) (KPI·Ablation은 `kpi_ablation_v12.md`)

## 1. 전체 흐름

```
접수 검사 → 분석 에이전트 → [ 수정 에이전트 → 공격 에이전트 → 판정 노드 ] → 사람 승인
 (LLM+코드)  (위치·근거·재현 테스트)   └──── 판정 실패 시 수정부터 다시 (재시도 최대 3회) ┘
```

- 워크플로는 프레임워크 없이 Python으로 직접 짠 고정 그래프입니다(`src/sr_agent/workflow/graph.py`).
- 판정 노드(`workflow/judge.py`)에는 LLM 호출이 없습니다. 통과 조건은 재현 테스트, 기존 테스트 1,145개, 정답 구현과 입력 200개 비교, 공격 입력 재실행입니다.
- 원본(`target/`)은 읽기 전용입니다. 티켓마다 `runs/<ticket_id>/original/`·`work/`로 복사하고, 수정은 작업본에서만 합니다.

## 2. 폴더 구조

| 경로 | 내용 |
|---|---|
| `src/sr_agent/workflow/` | 워크플로 그래프, 접수 검사(`intake.py`), 공격 단계(`attack.py`), 판정 노드(`judge.py`), 정답 구현 준비(`reference.py`), 티켓 상태(`state.py`) |
| `src/sr_agent/agents/` | 에이전트 루프(OpenAI Responses API + function calling), 에이전트별 사양·허용 도구, 프롬프트(`prompts/`) |
| `src/sr_agent/tools/` | 도구 함수: 작업본 관리, 테스트 실행, 코드 읽기·패치, 차이 비교, 속성 공격, 규칙·이력 검색, 위치 예측, 보안·표준 검사 |
| `src/sr_agent/ml/` | 결함 위치 예측 모델: 피처(`features.py`), 학습·평가(`train.py`), 지표(`metrics.py`), 예측 신호 기준(`calibrate.py`) |
| `src/sr_agent/rag/` | 사내 규칙·장애 이력 지식 베이스와 Chroma 색인(`index.py`) |
| `src/sr_agent/datagen/` | 데이터 생성: AST 뮤테이션(`mutate.py`), LLM 결함 주입(`llm_inject.py`), 테스트로 선별(`inject.py`), 티켓 작성(`tickets.py`), 분할·누수 검사(`split.py`) |
| `src/sr_agent/config.py` | 모델 ID, 타임아웃, 재시도 횟수, 경로 등 설정값 |
| `app/streamlit_app.py` | 담당자 화면 (Streamlit). 실시간 실행과 재생 모드 |
| `eval/` | E2E 배치(`run_e2e.py`), Ablation(`run_ablation.py`), 공격 탐지율(`run_attack_kpi.py`), 결과(`results/`) |
| `tests/` | 에이전트 시스템 자체의 테스트 (398개, LLM 호출 테스트는 기본 실행에서 제외) |
| `target/order_settlement/` | 수정 대상인 가상 레거시 프로젝트 (주문·정산 등 모듈 20개, 함수 268개, 테스트 1,145개). 레거시 스타일과 심어 둔 결함은 시연 재료라 의도된 것입니다 |
| `data/` | 데이터 파일 (3장) |
| `presentation/` | 최종 발표 자료와 진행 가이드 |

## 3. 데이터 파일 (`data/`)

| 파일 | 내용 | 만드는 명령 |
|---|---|---|
| `mutants/mutants.jsonl` | 결함 1,341개 (AST 853 + LLM 488). 정답 함수, 실패 테스트, 트레이스, diff | `datagen.llm_inject` → `datagen.inject` |
| `mutants/llm_bugs_raw.jsonl`, `summary.json` | LLM 결함 제안 원본, 연산자·결과별 집계 | 위와 같음 |
| `tickets/tickets.csv` | 티켓 2,678건 (결함당 운영·QA 2건). 제목·본문·스택트레이스·정답 함수·분할 | `datagen.tickets` → `datagen.split` |
| `tickets/drafts.jsonl`, `split.json` | 티켓 LLM 응답 원본, 결함 단위 분할 정보 (학습 1,057 / 평가 262 / 홀드아웃 20) | 위와 같음 |
| `rules/rules.json` | 가상 사내 규칙 40건 (업무·보안·표준·스타일) | 직접 작성 |
| `models/fault_ranker*.json` | 학습된 결함 위치 예측 모델(XGBoost Ranker)과 메타 정보, 예측 신호 기준 | `ml.train`, `ml.calibrate` |
| `scenarios.json` | 시연 시나리오 S1~S3 (SR-2041·2077·2103): 티켓, 판정 기준, 사람이 쓴 정답 구현 | 직접 작성 |
| `demo_cases.json` | 시연 케이스 A~D (홀드아웃 결함, 정답 없는 새 티켓) | 직접 작성 |
| `replay/` | 시연 재생 모드용 실제 실행 기록 (S1~S3, DEMO-A~D) | `sr_agent.ui save-replay` |
| `attack_kpi/patches.json` | 공격 탐지율 측정용 불완전 패치 (사람이 작성) | 직접 작성 |

모든 데이터는 가상으로 만든 것이며 실제 사내 코드·문서는 들어 있지 않습니다. 티켓 문장과 LLM 결함은 `gpt-6-luna`로 생성했습니다(약 $0.44).

## 4. 설치와 실행 (Windows PowerShell, Python 3.12)

```powershell
uv venv .venv --python 3.12
.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
# 프로젝트 루트에 .env 파일을 만들고 OPENAI_API_KEY=... 를 적는다
# (선택) REDFIX_LIVE_PASSWORD=... 를 적으면 화면의 실시간 모드에 비밀번호가 걸린다

python -m sr_agent.rag.index          # (PYTHONPATH=src) 규칙·이력 Chroma 색인 생성, 처음 한 번 약 5분
streamlit run app/streamlit_app.py    # 담당자 화면. 재생 모드는 API 키 없이도 동작
```

제출 zip에서는 데이터가 `3_데이터/data/`에 따로 들어 있습니다. 실행하려면 이 `data/` 폴더를 소스 폴더(이 README가 있는 곳) 안으로 옮깁니다.

`python -m` 명령은 프로젝트 루트에서 `$env:PYTHONPATH = "src"`를 설정한 뒤 실행합니다.

| 목적 | 명령 |
|---|---|
| 에이전트 시스템 테스트 | `pytest tests` |
| 대상 프로젝트 원본 테스트 | `pytest target/order_settlement/tests` |
| 티켓 하나 실행 (CLI) | `python -m sr_agent.run --ticket SR-2041 --restart` |
| E2E 홀드아웃 20건 | `python -m eval.run_e2e --split holdout --workers 2` (약 2시간, 약 $8) |
| Ablation 표 갱신 | `python -m eval.run_ablation --report-only --suite v12` |
| 공격 탐지율 | `python -m eval.run_attack_kpi` |
| 데이터 다시 만들기 | `datagen.llm_inject` → `datagen.inject` → `datagen.tickets` → `datagen.split` → `ml.train` (자세한 내용은 [docs/step2.md](docs/step2.md) 12장) |

## 5. 제출본에서 뺀 것

| 항목 | 이유 | 다시 만드는 방법 |
|---|---|---|
| `.env` | API 키 | 직접 작성 |
| `runs/` | 티켓별 작업본과 실행 로그 (약 220MB) | 실행하면 생김 |
| `data/chroma/`, `data/cache/` | 검색 색인과 임베딩 캐시 | `python -m sr_agent.rag.index`, 첫 학습·예측 때 자동 계산 |
| `.venv/` | 가상환경 | 4장 설치 |
