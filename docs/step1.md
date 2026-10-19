# Step 1 정리: 대상 프로젝트와 실행 도구 (F1-1 ~ F1-4)

> 작성일: 2026-10-19 · 범위: PRD 10장 1일차 · 상태: 완료
>
> 확인 결과: 원본 테스트 152 passed, 에이전트 시스템 테스트 70 passed, ruff 통과

이 문서는 Step 1에서 만든 파일이 무엇인지, 테스트가 무엇을 검사하는지, pytest가 어떻게 동작하는지 설명한다.

---

## 1. 한눈에 보기

Step 1의 목표는 에이전트가 고칠 **대상 코드**와, 그 코드를 **안전하게 실행하는 도구**를 만드는 것이다. 아직 LLM이나 에이전트는 없다.

```
target/order_settlement (원본, 읽기 전용)
        │  prepare_workspace("SR-2041")      ← F1-3
        ▼
runs/SR-2041/
├── original/     원본 복사본   ──┐
├── work/         작업본(수정)  ──┼─ run_tests / run_diff  ← F1-4
└── repro_tests/  재현 테스트   ──┘   (subprocess, 10초 타임아웃)
```

| PRD ID | 내용 | 결과물 |
|---|---|---|
| F1-1 | 주문·정산 가상 레거시 프로젝트 | `target/order_settlement/` (모듈 12개, 함수 108개, 테스트 152개) |
| F1-2 | 의도적 요소 (지원 종료 API, 보안 약점, S1 결함) | 위 프로젝트 안에 포함 |
| F1-3 | 원본 읽기 전용, 작업본에서만 수정 | `src/sr_agent/tools/workspace.py` |
| F1-4 | `run_tests`, `run_diff` 도구 | `src/sr_agent/tools/execution.py` |

---

## 2. 파일 구조와 역할

```
project/
├── pyproject.toml                      # 에이전트 시스템의 pytest·ruff 설정
├── requirements.txt                    # 의존성 (버전 고정)
├── docs/step1.md                       # 이 문서
├── target/order_settlement/            # [F1-1, F1-2] 대상 레거시 프로젝트
│   ├── pyproject.toml                  #   대상 프로젝트 자체의 pytest 설정
│   ├── src/order_settlement/*.py       #   업무 코드 12개 모듈
│   └── tests/*.py                      #   대상 프로젝트의 기존 테스트 152개
├── src/sr_agent/
│   ├── config.py                       # 설정값 한곳 (경로·타임아웃·시드·모델)
│   └── tools/
│       ├── workspace.py                # [F1-3] 작업본 생성·해시 검사·경로 거부
│       ├── execution.py                # [F1-4] run_tests, run_diff
│       ├── _pytest_report.py           #   자식 pytest에 붙는 결과 기록 플러그인
│       └── _diff_runner.py             #   run_diff가 띄우는 함수 실행기
└── tests/                              # 에이전트 시스템 자체의 테스트 70개
    ├── conftest.py                     #   공통 픽스처 (작은 가짜 대상 프로젝트)
    ├── test_config.py
    ├── test_workspace.py
    ├── test_execution.py
    └── test_target_integration.py
```

**테스트가 두 벌이라 헷갈리기 쉽다.**

| 구분 | 위치 | 무엇을 검사하나 | 실행 명령 |
|---|---|---|---|
| 대상 프로젝트 테스트 | `target/order_settlement/tests/` | 주문·정산 업무 코드가 맞게 동작하는지. 에이전트의 "정답"이며 **수정 금지** | `pytest target/order_settlement/tests` |
| 에이전트 시스템 테스트 | `tests/` | 우리가 만든 도구(작업본, 실행 도구)가 맞게 동작하는지 | `pytest tests` |

### 2.1 대상 프로젝트 `target/order_settlement/` (F1-1, F1-2)

2019~2021년에 운영된 주문·정산 시스템이라고 가정한 **가상 레거시** 코드다. Python 3.8 스타일로 썼고 3.12에서 실행된다.

| 모듈 | 역할 | 함수 수 | 의도적 요소 |
|---|---|---|---|
| `config` | 설정 상수, 공휴일 | 5 | `pytz` |
| `db` | SQLite 연결 (테스트는 인메모리) | 8 | |
| `customer_dao` | 고객 테이블 조회·저장 | 9 | **f-string SQL (S3)** |
| `customer` | 가입, 등급, 개인정보 마스킹 | 11 | 개인정보 로그, 예외 메시지에 개인정보 |
| `order` | 주문 생성·상태 변경 | 13 | `utcnow`, `%` SQL |
| `discount` | 등급·수량·쿠폰 할인 | 10 | **S1 결함** |
| `coupon` | 쿠폰 발급·검증 | 9 | `utcnow`, MD5 |
| `payment` | 결제·환불 | 9 | 카드번호 로그·저장, 예외 메시지 노출 |
| `settlement` | 판매자 정산 | 11 | **`utcnow` + `pytz` (S2)** |
| `date_utils` | 날짜·영업일 | 12 | `utcnow`, `pytz` |
| `report` | 운영 리포트 | 8 | f-string SQL, 개인정보 로그 |
| `errors` | 예외·오류 응답 | 3 | 트레이스백 노출 |

**시연 재료 위치**

| 시나리오 | 위치 | 원본에서 확인되는 증상 |
|---|---|---|
| S1 쿠폰 결함 | `discount.apply_coupon` | 10만 원 + 6만 원 쿠폰 2장 → 정산 금액 **-17,100원**. 정확히 10만 원 + 1만 원 쿠폰 2장 → 80,000원 (규칙상 90,000원) |
| S2 날짜 API | `settlement.calc_due_date` | `datetime.utcnow()`·`pytz` 사용. `-W error::DeprecationWarning`이면 테스트 실패 |
| S3 SQL 조립 | `customer_dao.find_by_name` | 입력 `' OR '1'='1` → 모든 고객 조회 |

**S1 결함의 구조:** `apply_coupon`은 최소 주문 금액을 "현재 금액"이 아니라 "원래 주문 금액"으로 판단하고, 0원 하한도 없다. 쿠폰이 1장이면 두 금액이 같아서 기존 테스트로는 잡히지 않는다. 올바른 규칙(`BIZ-CPN-01`, `BIZ-CPN-02`)은 코드에 쓰지 않았다. 2·3일차에 사내 규칙 KB에 넣어, 에이전트가 `search_rules`로 찾아 인용하게 한다.

### 2.2 `src/sr_agent/config.py`

설정값을 한곳에 모은 파일이다. 코드 여기저기에 숫자나 경로를 쓰지 않는다 (CLAUDE.md 규칙).

| 항목 | 값 | 의미 |
|---|---|---|
| `TARGET_ROOT` | `target/order_settlement` | 원본 위치 |
| `RUNS_DIR` | `runs/` | 티켓별 작업 디렉터리 |
| `SEED` | 42 | 난수 고정 (재현성) |
| `SUBPROCESS_TIMEOUT_S` | 10 | 코드 실행 제한 시간 |
| `MAX_FAILURES_REPORTED` / `MAX_TRACE_CHARS` | 10 / 2000 | 에이전트에게 넘길 실패 수·트레이스 길이 상한 |
| `MAX_DIFF_INPUTS` | 50 | `run_diff` 입력 개수 상한 |
| `SECRET_ENV_MARKERS` | `API_KEY`, `SECRET`, `TOKEN` | 자식 프로세스에 넘기지 않을 환경 변수 |
| `MODEL` / `MODEL_BULK` | `gpt-5` / `gpt-6-luna` | LLM 모델 (3일차부터 사용) |

### 2.3 `tools/workspace.py` (F1-3)

| 함수 | 하는 일 |
|---|---|
| `prepare_workspace(ticket_id)` | 원본을 `runs/<id>/original/`과 `work/` 두 벌로 복사하고 `repro_tests/`를 만든다. 이미 있으면 그대로 연다 (중단 후 이어서 실행). `overwrite=True`면 새로 복사 |
| `open_workspace(ticket_id)` | 이미 만든 작업 디렉터리 열기 |
| `validate_ticket_id` | `../evil`처럼 디렉터리 밖으로 나가는 ID를 거부 |
| `hash_tree(path)` | 폴더 안 파일마다 SHA-256 해시 계산 (`__pycache__` 등 캐시 제외) |
| `compare_hashes(before, after)` | 추가·삭제·변경된 파일 목록 |
| `verify_original_intact(ws)` | `original/` 복사본이 원본과 같은지 |
| `resolve_inside(base, path)` | 경로를 풀어서 `base` 밖이면 `WorkspaceError` |

**왜 두 벌로 복사하나:** 판정할 때 같은 테스트를 원본과 수정본에 각각 돌려 비교해야 한다. `target/`에서 직접 돌리면 캐시 파일이 생겨 "원본 해시 불변"(F1-3)을 보장하기 어렵다.

### 2.4 `tools/execution.py` (F1-4)

**`run_tests(ws, version, tests=None)`**
- `version="original"`이면 `original/`에서, `"patched"`면 `work/`에서 pytest를 실행한다.
- `tests=None`이면 대상 프로젝트 테스트 전체와 `repro_tests/`를 함께 돌린다.
- 반환: `{"passed": 152, "failed": [{"test": "tests/...::test_x", "trace": "..."}]}`
- 실패는 10건, 트레이스는 2000자까지만 넘긴다. 넘치면 `"omitted": 개수`를 붙인다.

**`run_diff(ws, method, inputs)`**
- 같은 입력으로 원본 함수와 수정본 함수를 각각 실행해 결과를 비교한다.
- `method`: `"discount.apply_coupons"` 형식
- `inputs`: 각 항목은 위치 인자 리스트 또는 `{"args": [...], "kwargs": {...}}`
  - JSON으로 표현할 수 없는 값: `{"$date": "2024-02-28"}`, `{"$datetime": "2024-01-31T16:00:00"}`, `{"$db": {"customers": [행, ...]}}`
- 반환: `[{"input", "original": {"ok": true, "value": ...}, "patched": {...}, "same": true}]`

**공통 안전장치**

| 장치 | 방법 |
|---|---|
| 타임아웃 | `subprocess.run(timeout=10)`. 넘으면 `{"error": "timeout..."}` |
| 예외 대신 오류 값 | 도구는 예외를 던지지 않고 `{"error": "..."}`를 돌려준다 (에이전트가 읽고 대응하도록) |
| 경로 거부 | 테스트 경로가 버전 디렉터리나 `repro_tests/` 밖이면 거부 |
| 비밀 값 차단 | 자식 프로세스 환경에서 `*API_KEY*` 등 제거. LLM이 만든 코드가 키를 읽지 못함 |
| 원본 오염 방지 | `PYTHONDONTWRITEBYTECODE=1`로 `__pycache__` 생성 막음 |

**보조 모듈**
- `_pytest_report.py`: 자식 pytest에 `-p`로 붙이는 플러그인. 테스트마다 결과(이름, 통과/실패, 트레이스)를 JSON 파일로 기록한다. 화면 출력을 파싱하는 것보다 정확하다.
- `_diff_runner.py`: `run_diff`가 원본·수정본마다 하나씩 띄우는 프로세스. 함수를 import해 입력마다 실행하고 결과를 JSON으로 출력한다. 대상 코드의 `print`가 결과를 망치지 않도록 출력을 가로챈다.

---

## 3. pytest란 무엇인가

pytest는 Python **테스트 실행기**다. 테스트 파일을 찾아 함수를 실행하고, `assert`가 하나라도 틀리면 그 테스트를 실패로 기록해 보고한다.

### 3.1 기본 동작 순서

```
pytest tests
  1. 설정 읽기      pyproject.toml의 [tool.pytest.ini_options]
  2. 테스트 수집    tests/ 아래 test_*.py 파일 → test_로 시작하는 함수
  3. 픽스처 준비    함수 인자 이름과 같은 픽스처를 찾아 먼저 실행
  4. 테스트 실행    함수 본문 실행, assert 검사
  5. 결과 보고      . (통과)  F (실패)  E (준비 단계 오류)  s (건너뜀)
```

출력 예:
```
........................................................................   [100%]
70 passed in 35.07s
```
점 하나가 테스트 하나다. 실패가 있으면 아래에 어느 줄의 어떤 값이 달랐는지 자세히 나온다.

### 3.2 이 프로젝트에서 쓰는 pytest 기능

| 기능 | 의미 | 예시 위치 |
|---|---|---|
| `assert` | 조건이 거짓이면 실패. 실패하면 pytest가 양쪽 값을 비교해서 보여 줌 | 모든 테스트 |
| **픽스처** (`@pytest.fixture`) | 테스트 준비물을 만드는 함수. 테스트 인자 이름이 같으면 자동으로 넣어 줌 | `tests/conftest.py`의 `ws`, `mini_target` |
| `conftest.py` | 같은 폴더의 테스트들이 공유하는 픽스처를 두는 파일 (import 없이 사용) | `tests/conftest.py`, `target/.../tests/conftest.py` |
| `tmp_path` | pytest가 테스트마다 만들어 주는 빈 임시 폴더. 프로젝트를 더럽히지 않음 | 작업본을 `tmp_path/runs`에 생성 |
| `@pytest.mark.parametrize` | 같은 테스트를 입력만 바꿔 여러 번 실행 | `test_invalid_ticket_id_rejected` (9가지 ID) |
| `pytest.raises(예외)` | 해당 예외가 **나야** 통과 | 잘못된 경로 거부 테스트 |
| `monkeypatch` | 테스트 동안만 환경 변수·함수를 바꿨다가 자동 복구 | `test_secrets_not_passed_to_child` |
| 마커 (`@pytest.mark.llm`) | 테스트에 꼬리표를 붙여 골라 실행. `llm` 테스트는 기본 제외 (API 비용) | 3일차부터 사용 |

**픽스처 예시** (`tests/conftest.py`)
```python
@pytest.fixture
def ws(mini_target, tmp_path):          # mini_target 픽스처와 tmp_path를 받아서
    return prepare_workspace("SR-0001", source=mini_target, runs_dir=tmp_path / "runs")

def test_run_tests_original_passes(ws):  # 인자 이름이 ws → 위 픽스처 결과가 들어옴
    assert run_tests(ws, "original") == {"passed": 2, "failed": []}
```
테스트마다 새 임시 작업본을 받으므로, 한 테스트가 파일을 고쳐도 다른 테스트에 영향이 없다.

### 3.3 설정 파일 (`pyproject.toml`)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]                          # 경로를 안 주면 tests/에서 찾기
pythonpath = ["src"]                           # src/를 import 경로에 추가 → import sr_agent 가능
addopts = "-m 'not llm' -p no:cacheprovider"   # 기본으로 llm 테스트 제외, .pytest_cache 안 만듦
markers = ["llm: ..."]                         # 사용하는 마커 등록
```

대상 프로젝트도 자기 `pyproject.toml`을 갖고 있다 (`pythonpath = ["src"]`). pytest는 **실행할 경로에서 가장 가까운 설정 파일**을 쓴다. 그래서 `pytest target/order_settlement/tests`는 대상 프로젝트 설정을, `pytest tests`는 루트 설정을 쓴다.

### 3.4 자주 쓰는 명령

| 명령 | 용도 |
|---|---|
| `pytest tests -q` | 짧게 결과만 |
| `pytest tests -v` | 테스트 이름별로 PASSED/FAILED 표시 |
| `pytest tests/test_workspace.py` | 파일 하나만 |
| `pytest tests/test_execution.py::test_run_diff_same` | 테스트 하나만 |
| `pytest tests -k timeout` | 이름에 `timeout`이 들어간 테스트만 |
| `pytest tests -x` | 첫 실패에서 멈춤 |
| `pytest tests --collect-only -q` | 실행하지 않고 어떤 테스트가 있는지만 보기 |
| `pytest -m llm` | LLM 테스트만 (3일차 이후, 비용 발생) |

### 3.5 `run_tests` 도구 안에서의 pytest

`run_tests`도 내부에서 pytest를 쓴다. 다만 **별도 프로세스**로 실행한다.

```
run_tests(ws, "patched")
  └─ subprocess: python -m pytest -p sr_agent.tools._pytest_report
                   --rootdir runs/<id>/work -c runs/<id>/work/pyproject.toml
                   tests  runs/<id>/repro_tests
       ├─ 작업 디렉터리: work/          (작업본 기준으로 실행)
       ├─ 환경 변수: API 키 제거, 바이트코드 끔
       ├─ 10초 넘으면 강제 종료
       └─ 플러그인이 결과를 runs/<id>/.reports/pytest_patched.json에 기록
  └─ JSON을 읽어 {"passed", "failed"}로 요약
```

별도 프로세스로 돌리는 이유:
- **격리:** 에이전트가 만든 코드가 무한 루프를 돌거나 이상한 동작을 해도 에이전트 프로세스는 안전하다.
- **타임아웃:** 프로세스째 끊을 수 있다.
- **버전 분리:** 원본과 수정본이 같은 모듈 이름(`order_settlement`)을 쓰므로, 한 프로세스에서 둘을 동시에 import할 수 없다.

---

## 4. 테스트별 설명 (에이전트 시스템 `tests/`, 70개)

괄호 안 숫자는 parametrize로 늘어난 실행 횟수다.

### 4.1 `tests/conftest.py`: 공통 준비물

- `mini_target`: 함수 5개짜리 **작은 가짜 대상 프로젝트**를 임시 폴더에 만든다. 진짜 `target/`(테스트 152개)을 쓰면 느리고, 무한 루프 같은 일부러 이상한 코드를 넣을 수 없기 때문이다.
  - `add`(덧셈), `divide`(0으로 나누면 예외), `next_day`(날짜), `shout`(`print` 소음), `spin`(무한 루프)
- `ws`: 위 프로젝트로 만든 작업 디렉터리
- `work_calc`: 작업본의 `calc.py` 경로 (수정 테스트용)
- `write`, `replace_in`: 파일 쓰기·문자열 치환 도우미

### 4.2 `test_config.py` (3개): 설정값

| 테스트 | 확인하는 것 |
|---|---|
| `test_paths_point_to_project` | 설정 경로가 실제 프로젝트·대상 프로젝트를 가리키는지 |
| `test_execution_limits` | 타임아웃 10초, 재시도 3회, 시드가 정수인지 |
| `test_model_ids` | 모델이 `gpt-5` / `gpt-6-luna`인지 (실수로 바뀌는 것 방지) |

### 4.3 `test_workspace.py` (23개): 작업본 (F1-3)

| 테스트 | 확인하는 것 |
|---|---|
| `test_prepare_creates_two_identical_copies` | `original/`·`work/`·`repro_tests/`가 생기고, 두 복사본이 원본과 해시까지 같은지 |
| `test_prepare_skips_cache_files` | `__pycache__`는 복사·해시 대상에서 빠지는지 |
| `test_invalid_ticket_id_rejected` (9) | `""`, `../evil`, `a/b`, `a\b`, `..`, `-x`, 공백, 65자, `None`을 거부하고 밖에 폴더를 만들지 않는지 |
| `test_missing_source_rejected` | 없는 원본 경로를 거부하는지 |
| `test_reopen_keeps_work_and_overwrite_resets` | 다시 열면 작업 내용 유지(이어서 실행), `overwrite=True`면 초기화 |
| `test_open_workspace` | 있는 작업본은 열리고, 없으면 오류 |
| `test_compare_hashes_detects_changes` | 파일 추가·삭제·수정을 각각 정확히 찾는지 |
| `test_verify_original_intact` | `original/`이 바뀌면 탐지하는지 |
| `test_resolve_accepts_inside` | 작업본 안 경로는 허용 |
| `test_resolve_rejects_outside` (4) | `../original/...`, `../../`, `src/../../`, 역슬래시 경로 거부 |
| `test_resolve_rejects_absolute_outside` | 작업본 밖 절대 경로 거부 |
| `test_unknown_version_rejected` | `original`/`patched` 외 버전 이름 거부 |

### 4.4 `test_execution.py` (40개): 실행 도구 (F1-4)

**run_tests (18개)**

| 테스트 | 확인하는 것 |
|---|---|
| `test_run_tests_original_passes` | 원본 실행 결과 형식 `{"passed": 2, "failed": []}` |
| `test_run_tests_reports_failure_only_in_patched` | 작업본만 고장 내면 patched에서만 실패하고 원본은 그대로 통과. 실패 이름·트레이스 포함 |
| `test_run_tests_subset` | 특정 테스트 하나만 지정해서 실행 |
| `test_run_tests_rejects_outside_paths` (3) | 테스트 경로로 작업본 밖을 가리키면 거부 |
| `test_run_tests_rejects_absolute_outside` | 작업본 밖 절대 경로의 테스트 파일 거부 |
| `test_run_tests_invalid_arguments` (3) | 잘못된 버전, 빈 목록, 없는 파일 → `error` |
| `test_repro_test_fails_on_original_and_passes_on_patched` | **재현 테스트 흐름**: 원본에서 실패 → 작업본 수정 → 통과. 이름이 `repro_tests/...`로 표시되는지 |
| `test_run_tests_timeout` | 무한 루프 테스트가 제한 시간에 끊기고 `timeout` 오류 반환 |
| `test_run_tests_default_timeout_is_config` | 기본 타임아웃이 설정값 10초인지 |
| `test_collection_error_reported` | 문법 오류로 테스트를 불러오지 못해도 실패로 보고 (`SyntaxError`) |
| `test_trace_is_trimmed` | 긴 트레이스가 2000자 근처로 잘리는지 |
| `test_failures_are_capped` | 실패 15건 → 10건만 보내고 `omitted: 5` |
| `test_secrets_not_passed_to_child` | API 키 환경 변수가 자식 프로세스에 보이지 않는지 |
| `test_run_tests_leaves_original_untouched` | 실행 후에도 `original/` 해시 그대로, `__pycache__` 없음 |

**run_diff (22개)**

| 테스트 | 확인하는 것 |
|---|---|
| `test_run_diff_same` | 리스트·`{args, kwargs}` 두 입력 형식 모두 동작, 결과 같으면 `same: true` |
| `test_run_diff_detects_difference` | 작업본을 바꾸면 해당 입력만 `same: false` |
| `test_run_diff_captures_exceptions` | 예외도 결과로 기록 (`ZeroDivisionError: division by zero`) |
| `test_run_diff_date_values_and_package_prefix` | `$date` 입력·출력 변환, `order_settlement.` 접두어 허용 |
| `test_run_diff_ignores_stdout_noise` | 대상 코드의 `print`가 결과를 망치지 않는지 |
| `test_run_diff_rejects_bad_method` (6) | 모듈 없는 이름, 빈 문자열, 코드 주입 시도, 상대 경로, 괄호, 숫자 거부 |
| `test_run_diff_missing_function` | 없는 함수 → 오류 메시지 |
| `test_run_diff_rejects_bad_inputs` (7) | 빈 목록, 문자열, 리스트 아닌 항목, 잘못된 dict 키, 잘못된 `args` 타입, JSON 불가 값, 50개 초과 거부 |
| `test_run_diff_timeout` | 무한 루프 함수가 제한 시간에 끊김 |
| `test_run_diff_patched_import_error` | 수정본에 문법 오류 → `patched: 모듈 import 실패` |
| `test_run_diff_long_value_is_shortened` | 긴 결과는 잘라서 보여 주되, 비교는 전체 값으로 |

### 4.5 `test_target_integration.py` (4개): 진짜 `target/`으로 확인

| 테스트 | 확인하는 것 |
|---|---|
| `test_original_tests_pass_and_target_unchanged` | 작업본에서 원본 테스트 150개 이상 통과 + **실행 전후 `target/` 해시 동일** (F1-3 수용 기준) |
| `test_s1_repro_fails_on_original_and_fix_passes` | S1 재현 테스트 원본 실패 → 수정 → 전체 통과. `run_diff`에서 경계 입력(10만 원)만 결과가 80,000 → 90,000으로 바뀜 |
| `test_s2_due_date_with_datetime_input` | S2 대상 함수를 `$datetime` 입력으로 실행 (`2024-03-08`) |
| `test_s3_injection_detected_by_run_diff` | 원본은 인젝션으로 2명 조회, 파라미터 바인딩으로 고친 뒤 정상 입력은 동일·인젝션 입력은 빈 결과 |

---

## 5. 대상 프로젝트 테스트 (`target/order_settlement/tests/`, 152개)

업무 코드의 **기존 동작**을 고정하는 테스트다. 에이전트의 패치가 이 테스트를 깨면 회귀다.

| 파일 | 개수 | 주요 내용 |
|---|---|---|
| `test_config.py` | 5 | 설정 조회, 오버라이드, 시간대, 수수료율, 공휴일 |
| `test_db.py` | 5 | 스키마, insert·조회, 공용 커넥션 |
| `test_customer_dao.py` | 10 | 조회(동명이인), 등급 변경, 삭제 |
| `test_customer.py` | 29 | 이메일·전화 검증, 마스킹, 가입, 등급 산정·상향 |
| `test_order.py` | 18 | 주문 생성, 상품, 배송비 경계(49,999 / 50,000), 상태 전이 |
| `test_discount.py` | 18 | 절사, 등급 할인, 상한, 쿠폰 1장 적용, 30만 원에 쿠폰 2장 |
| `test_coupon.py` | 11 | 발급, 만료, 검증 사유, 사용 처리 |
| `test_payment.py` | 19 | Luhn 검사, 수수료, 결제, 환불 수수료 경계(7·8·30·31일), 부분 환불 |
| `test_date_utils.py` | 12 | KST 변환, 영업일(공휴일·주말), 윤년, 월말 |
| `test_settlement.py` | 13 | 수수료, 정산 금액, 지급일(월 경계), 정산 생성·마감 |
| `test_report.py` | 8 | 일·월 매출, 우수 고객, CSV |
| `test_errors.py` | 4 | 오류 응답, 안전한 정수 변환 |

**일부러 빠뜨린 것:** 쿠폰 2장이 결함을 드러내는 경우(10만 원대 주문)는 테스트하지 않는다. 그래서 S1 결함이 있는데도 152개가 모두 통과한다. 이 공백을 재현 테스트와 공격 에이전트가 찾아야 한다.

**`test_due_date_default_now`:** 인자 없이 `calc_due_date()`를 호출한다. `-W error::DeprecationWarning`으로 돌리면 원본은 `utcnow` 때문에 실패하고, 올바르게 고친 버전은 통과한다. S2 판정이 의미를 갖게 하려고 넣은 테스트다.

---

## 6. PRD와 다르게 정한 것 (PRD v0.2에 반영됨)

| 항목 | 결정 | 이유 |
|---|---|---|
| 도구 첫 인자 `ws` | Python 함수는 `ws`를 받고, LLM에는 바인딩해서 PRD 인자만 노출 | PRD 시그니처에 티켓 정보가 없음 |
| `run_diff` 입력 표기 | `$date`, `$datetime`, `$db` | 날짜·DB 연결은 JSON으로 못 넘김 |
| `apply_patch`의 `imports` 인자 | 같은 파일의 import만 추가·삭제 허용 | S2는 `import pytz` 제거가 필요 |
| 판정 "0건" 범위 | 패치한 메서드 + 바뀐 import 줄 | 다른 티켓용 재료 때문에 S2·S3가 항상 실패하는 것 방지 |
| S1 업무 규칙 위치 | 코드 docstring에서 빼고 규칙 KB로 | 결함 위치가 너무 쉽게 드러나지 않게, 근거 인용 흐름 만들기 |

---

## 7. 직접 확인하는 방법 (요약)

```powershell
.venv\Scripts\Activate.ps1
pytest target/order_settlement/tests -q     # 152 passed
pytest tests -v                             # 70 passed, 항목별 이름 표시
ruff check src tests
```

도구를 손으로 써 보려면:

```powershell
$env:PYTHONPATH = "src"; python
```
```python
from sr_agent.tools.workspace import prepare_workspace
from sr_agent.tools.execution import run_tests, run_diff
ws = prepare_workspace("SR-DEMO")           # runs/SR-DEMO/ 생성
run_tests(ws, "original")
c = {"type": "FIXED", "value": 10000, "min_order": 100000}
run_diff(ws, "discount.apply_coupons", [[100000, [c, c]]])
```

끝나면 `Remove-Item -Recurse -Force runs\SR-DEMO`로 정리한다.
