# Step 4 정리: 공격 에이전트, 판정 3유형, Streamlit 화면, E2E 배치 (F4-3, F5-3 ~ F5-6, F6, F7-2)

> 작성일: 2026-10-21 · 범위: PRD 10장 4일차 · 상태: 완료
>
> 확인 결과: 에이전트 시스템 테스트 322 passed (+ 실제 API 테스트 3개, `pytest -m llm`), 대상 프로젝트 원본 테스트 1,145 passed.
> **S1~S3가 화면에서 동작**: 세 시나리오 모두 판정 통과 → 승인 대기. 시연용 재생 데이터 저장 완료.
> **E2E 홀드아웃 20건**: 17건 해결(85%), 에이전트 최종 위치 정답 100%, $6.07

이 문서는 4일차에 만든 공격 에이전트, 코드 표준화·보안 판정, Streamlit 화면과 재생 모드, E2E 배치를 설명하고, S1~S3와 홀드아웃 20건을 실제로 돌린 결과를 보여 준다.
앞 단계는 [step1.md](step1.md)(대상 프로젝트·실행 도구), [step2.md](step2.md)(결함 데이터·예측 모델), [step3.md](step3.md)(RAG·분석·수정 에이전트)에 있다.

---

## 1. 한눈에 보기

### 1.1 무엇을 만들었나

3일차에는 결함 수정 유형(S1)만 "분석 → 수정 → 판정"으로 처리할 수 있었다. 4일차에는 다음을 더했다.

- 고친 코드를 일부러 깨뜨려 보는 **공격 에이전트**
- 나머지 두 수정 유형(코드 표준화, 보안 조치)의 **판정**
- 사람이 보고 승인하는 **화면**
- 결과를 숫자로 확인하는 **E2E 배치**

```
SR 티켓
   │
   ├─ 결함 수정(defect) ──→ [분석 에이전트] → [재현 확인: 원본 실패 + 정답 구현 통과]
   │                                                  │
   ├─ 코드 표준화(standardize) ─ 분석 생략 ─────────────┤
   └─ 보안 조치(security) ────── 분석 생략 ─────────────┤
                                                      ▼
                                              [수정 에이전트]
                                                      │
                                                      ▼
                                              [공격 에이전트]  gpt-5 (추론 medium)
                                                      │  발견 → run_diff로 다시 실행해 확인(verified)
                                                      │       → 확인된 것만 공격 이력(Chroma)에 저장
                                                      ▼
                                              [판정 노드]  코드만, 유형별 조건 (PRD 7.2)
                                                      │  실패 → 실패 조건 + 확인된 공격 발견을 붙여 수정 재시도 (최대 3회)
                                                      ▼
                                              [사람 승인]  Streamlit: 승인(판정 통과 시에만) · 반려(사유 필수)
```

코드 표준화·보안 유형은 진단 결과가 위치를 알려 주므로 분석을 건너뛰고 티켓에 적힌 대상 함수(`method`)부터 수정한다 (PRD 12장).

### 1.2 결과 요약

| 항목 | 값 |
|---|---|
| S1 결함 수정 (SR-2041) | 판정 5조건 통과, 374초, 23.6만 토큰, 약 $0.27 |
| S2 코드 표준화 (SR-2077) | 판정 6조건 통과 (1개는 해당 없음), 232초, 12.8만 토큰, 약 $0.16 |
| S3 보안 조치 (SR-2103) | 판정 5조건 통과, 공격 입력 4종 무력화, 165초, 11.0만 토큰, 약 $0.12 |
| E2E 홀드아웃 20건 | **해결 17건 (85%, 목표 60%)**, 모델 Top-3 85%, 에이전트 최종 위치 100% |
| E2E 비용·시간 | 합계 $6.07, 티켓당 평균 26.4만 토큰·351초 |
| 에이전트 도구 | PRD 7.3의 15개 모두 구현 (4일차에 공격 3개·검사 3개 추가) |
| 테스트 | 259 → **322개** (+ 실제 API 2 → 3개) |

### 1.3 PRD 기능별 결과물

| PRD ID | 내용 | 결과물 |
|---|---|---|
| F5-3 | 공격 에이전트 (미달·회귀, 발견 확인) | `agents/prompts/attack.md`, `workflow/attack.py`, `tools/attack.py`, `tools/_property_runner.py` |
| F4-3 | 공격 이력 (확인된 공격만 저장, 검색해 재사용) | `tools/attack.py`의 `attack_memory`, Chroma `attacks` |
| F5-4 | 판정 3유형, 참조 구현 비교 | `workflow/judge.py`, `workflow/reference.py`, `data/scenarios.json`의 `judge` |
| (검사 도구) | `check_deprecated`, `check_style`, `check_security` | `tools/checks.py` |
| F5-5 | 유형별 워크플로, 재시도에 공격 발견 포함 | `workflow/graph.py` |
| F5-6, F6 | 화면, 승인·반려, 재생 모드 | `app/streamlit_app.py`, `src/sr_agent/ui.py`, `data/replay/` |
| F7-2 | E2E 배치 | `src/sr_agent/e2e.py`, `eval/run_e2e.py`, `eval/results/e2e_full.*` |
| F7-3 (준비) | Ablation 실행 옵션 | `--no-predict`, `--no-attack` (실행은 5일차) |

---

## 2. 공격 에이전트 (F5-3, F4-3)

### 2.1 역할

수정 에이전트의 패치에서 두 가지 문제를 찾는다.

| 종류 | 뜻 | 기대값 |
|---|---|---|
| **수정 미달** (under_fix) | 결함을 덜 고쳤다 | 근거 규칙으로 계산한 값. 근거 `rule_id`가 없으면 미달로 보고하지 않는다 |
| **회귀** (regression) | 고치지 말아야 할 동작이 바뀌었다 | 원본 결과 |

순서는 프롬프트(`agents/prompts/attack.md`)로 정했다.

1. `attack_memory(search)`로 같은 유형의 과거 성공 공격을 먼저 확인한다.
2. 수정된 함수와 호출부를 읽는다.
3. 근거 규칙의 경계값 바로 아래·같은 값·바로 위, 0·음수·큰 값, 여러 개 조합으로 `run_diff`를 돌린다 (미달 공격).
4. `property_attack`으로 넓은 입력에서 원본과 수정본을 비교한다 (회귀 공격).

공격 에이전트는 **코드를 고치지 않고, 통과·실패도 정하지 않는다.** 발견은 판정과 재시도에 넘기는 입력일 뿐이다.

### 2.2 도구

| 도구 | 하는 일 |
|---|---|
| `run_diff(method, inputs_json)` | LLM이 설계한 입력으로 원본·수정본을 각각 실행해 결과 비교 |
| `property_attack(method, strategy_json, max_examples)` | Hypothesis로 입력을 만들어 차등 실행(시드 고정). 불일치는 최소 사례로 줄여서 돌려준다 |
| `attack_memory(op, attack_type, record_json)` | 공격 이력 검색. 공격 에이전트는 **검색만** 하고, 저장은 워크플로가 한다 |

- 입력 표기: 위치 인자 리스트 또는 `{"args": [...], "kwargs": {...}}`. JSON으로 쓸 수 없는 값은 `{"$datetime": ISO}`, `{"$date": ...}`, `{"$db": {테이블: [행, ...]}}`로 쓴다. `$db`는 입력마다 대상 프로젝트 스키마의 인메모리 DB를 새로 만든다 (S3처럼 DB 연결을 받는 함수도 공격할 수 있게).
- `_property_runner.py`는 원본·수정본·참조 구현(정답)을 한 프로세스에 올려 같은 입력으로 비교한다. 판정 노드도 같은 실행기를 쓴다.

### 2.3 발견 확인과 저장

LLM이 보고한 발견을 그대로 믿지 않는다. 워크플로가 `run_diff`로 **다시 실행해 확인**한다 (`workflow/attack.py`의 `verify_finding`).

| 종류 | 확인(verified) 조건 |
|---|---|
| 미달 | 수정본 결과가 규칙상 기대값과 다름 |
| 회귀 | 수정본 결과가 원본과 다름 |

확인된 발견만 공격 이력(Chroma `attacks`)에 저장하고, 재시도 때 수정 에이전트 입력에 붙이며, 화면의 공격 로그에 표시한다. 확인되지 않은 발견은 이유(`check`)와 함께 로그에만 남는다.

---

## 3. 판정 3유형 (F5-4)

판정은 모두 코드로 한다. `judge.py`가 LLM 모듈을 import하지 않는지 테스트로 확인한다.

### 3.1 결함 수정 (S1, E2E)

| 조건 | 확인 방법 |
|---|---|
| `repro_original_fails` | 재현 테스트가 원본에서 단언 실패로 실패 |
| `repro_patched_passes` | 재현 테스트가 수정본에서 통과 |
| `existing_tests_pass` | 기존 테스트 1,145개 통과 |
| `change_scope` | 대상 함수와 import만 변경 |
| `reference_property` | **Hypothesis 입력 200개에서 수정본 = 참조 구현(정답).** 불일치는 원본도 정답과 달랐던 입력이면 미달, 원본은 맞았던 입력이면 회귀로 분류 |
| `hidden_tests_pass` | (E2E만) 결함 때문에 실패하던 기존 테스트를 숨겨 두었다가 판정에서만 실행 |

3일차에 미결이던 "결함 영역 밖 입력은 원본과 동일" 조건은 영역을 따로 정의하지 않고 **정답 구현 비교로 대체**했다 (PRD v0.8). 원본과 정답이 같은 입력이 곧 영역 밖이고, 거기서 수정본이 다르면 회귀다.

참조 구현은 에이전트에게 보여 주지 않는다. 시연 시나리오는 `data/scenarios.json`의 `judge`(진입 함수, 입력 생성 규칙, 사람이 쓴 정답 함수)를 쓰고, E2E 결함은 결함을 심기 전 원래 함수를 쓴다.

**재현 테스트도 정답 구현으로 검증한다.** 재현 테스트는 정답 구현에서 통과해야 분석 결과로 받는다. 틀리면 정답 코드는 숨긴 채 "기대값이 업무 규칙과 다름"만 알려 분석을 다시 시킨다 (분석 시도 상한 3).

### 3.2 코드 표준화 (S2)

| 조건 | 확인 방법 |
|---|---|
| `behavior_preserved` | Hypothesis 입력 200개에서 원본과 출력 완전 동일 (월말·KST 자정·윤년 경계 시각 + 2024~2025년 임의 시각) |
| `attack_regressions_preserved` | 공격 에이전트가 제안한 회귀 입력을 판정이 다시 실행해 원본과 비교 (LLM은 입력만 제안, 판단은 코드) |
| `deprecated_zero` | `check_deprecated` 0건 (`DEP-*`). 패치로 쓰지 않게 된 지원 종료 import(예: `import pytz`)가 남아 있어도 위반 |
| `style_zero` | `check_style` 0건 (`STY-01`~`06`) |
| `existing_tests_pass` | 기존 테스트 통과. 대상 모듈의 DeprecationWarning은 오류로 처리 (`-W error::DeprecationWarning:order_settlement.<모듈>`) |
| `change_scope` | 대상 함수와 import만 변경 |

### 3.3 보안 조치 (S3)

| 조건 | 확인 방법 |
|---|---|
| `security_zero` | `check_security` 0건 (bandit + 사내 규칙 AST 검사. 예: B608 → `SEC-SQL-01`) |
| `attack_neutralized` | 시나리오에 정한 공격 입력 4종이 정한 안전한 결과(빈 결과)를 냄 |
| `normal_preserved` | 정상 입력 Hypothesis 200개에서 원본과 출력 동일 |
| `existing_tests_pass`, `change_scope` | 위와 같음 |

S3 공격 입력 4종: `' OR '1'='1`(항상 참 조건), `김민수' --`(주석으로 조건 무시), `x' UNION SELECT ...`(다른 행 조회), `O'Brien`(따옴표가 든 정상 이름이 오류 없이 빈 결과).

### 3.4 "0건"의 범위

대상 프로젝트에는 다른 티켓용 재료(`utcnow` 9곳, SQL 조립 7곳)가 일부러 남아 있다. 그래서 지원 종료 API·스타일·보안 "0건"은 **패치한 함수의 줄 범위와 패치로 바뀐 import 줄에서 나온 결과만** 센다 (PRD 7.2 검사 범위). 검사 도구 세 개에 범위 인자 `method`를 두었다. 테스트는 전체를 실행한다.

---

## 4. 화면과 재생 모드 (F6, F5-6)

### 4.1 화면 구성 (`app/streamlit_app.py`)

| 영역 | 내용 |
|---|---|
| 사이드바 | 모드 선택(재생·실시간), 티켓 목록(유형·상태), 새 티켓 등록(유형 선택), KPI 카드 3개(재시도 횟수, 확인된 미달·회귀 수, 테스트 통과 수) |
| 상단 | 진행 단계 5개 (분석 · 수정 · 공격 · 판정 · 승인)와 현재 위치, 판정 조건 요약 |
| 탭 ① 결함 위치 | 모델 Top-3 + SHAP 근거 3개와 에이전트 최종 판단(모델 순위, 판단 이유, 모델과 다른 이유)을 나란히 |
| 탭 ② 근거 | 인용한 이력 ID·규칙 ID와 본문 |
| 탭 ③ 패치 diff | 시도별 diff와 패치 설명 |
| 탭 ④ 공격 로그 | 시도한 공격, 발견(미달·회귀 구분), 확인 여부 |
| 하단 | 승인(판정을 통과했을 때만 활성화, 승인한 diff를 결과물로 확정) · 반려(사유 필수) |

실시간 모드는 `st.status`로 진행 단계를 보여 주며 실제로 실행한다. 승인·반려 결정은 티켓 상태에 기록된다.

### 4.2 재생 모드 (PRD 9장 시연 안정성)

시연 중 API 지연이나 실행마다 달라지는 결과에 대비해, 사전 실행 결과를 `data/replay/<티켓>/`에 저장해 두고 API 호출 없이 재생한다.

- `state.json`: 최종 상태 (분석, 예측, 시도별 패치·공격·판정)
- `events.jsonl`: 실행 이벤트. 재생할 때 진행 메시지로 다시 흘려보낸다
- 재생 모드의 승인·반려는 사본에 기록하므로 저장본은 바뀌지 않는다.
- 저장 명령: `python -m sr_agent.ui save-replay SR-2041 SR-2077 SR-2103`

---

## 5. S1~S3 실행 결과 (재생 데이터)

비용은 gpt-5 단가(100만 토큰당 입력 $1.25, 캐시 $0.125, 출력 $10)로 계산한 추정치다.

### 5.1 S1 결함 수정: 쿠폰 중복 적용 시 정산 금액 음수 (SR-2041)

- **분석**: 예측 Top-3는 `settlement.settle_order`, **`discount.apply_coupon`**, `discount.is_coupon_applicable`이다. 에이전트는 모델 2위 `apply_coupon`을 결함 위치로 확정했다.
  - 모델 1위를 고르지 않은 이유: "`settle_order`는 정산 파이프라인을 조립만 하고, 실제 금액 계산은 `calc_final_amount → apply_coupons → apply_coupon`에서 수행된다."
  - 재현 테스트: 주문 100,000원에 90,000원·20,000원 정액 쿠폰(각 최소 주문 금액 100,000원)을 적용하면 원본은 -10,000원이 나온다. 규칙상 기대값은 10,000원이다.
- **수정**: 3일차와 같은 두 줄 수정 (`order_amount` → `current_amount`, `max(0, ...)`).
  - 인용 규칙: `BIZ-CPN-01·02`
  - 인용 이력: `HIS-dc2217ee`, `HIS-f45ea821`, `HIS-217535f8`
- **공격**: 결과는 미달 0건, 회귀 0건.
  - 미달 공격 3회: 현재 금액 10,000원에 최소 주문 금액 100,000원 쿠폰, 5,000원에 20,000원 쿠폰, 0원에 1,000원 쿠폰.
  - 회귀 공격 1회: 단일 쿠폰 영역 200개.
- **판정**: 5조건 모두 통과. `reference_property`는 입력 200개 중 정답과 다른 입력 0개였다.

| 에이전트 | LLM 호출 | 입력 토큰 (캐시) | 출력 토큰 | 비용 |
|---|---|---|---|---|
| 분석 | 16 | 115,607 (109,440) | 12,289 | $0.144 |
| 수정 | 5 | 15,686 (14,080) | 1,882 | $0.023 |
| 공격 | 14 | 82,322 (78,848) | 8,374 | $0.098 |
| 합계 | 35 | | | **$0.27**, 374초 |

### 5.2 S2 코드 표준화: 날짜 API 교체 + 팀 스타일 (SR-2077)

```diff
-import pytz
+from datetime import timezone
+from zoneinfo import ZoneInfo
 ...
-def calc_due_date(order_dt=None):
+def calc_due_date(order_dt: datetime | None = None) -> date:
     ...
     if order_dt is None:
-        order_dt = datetime.utcnow()
-    kst = pytz.timezone(config.TIMEZONE_NAME)
-    local = pytz.utc.localize(order_dt).astimezone(kst)
+        utc_dt = datetime.now(timezone.utc)
+    else:
+        # 기존 동작과 동일하게 naive UTC만 허용한다.
+        if order_dt.tzinfo is not None:
+            raise ValueError("Not naive datetime (tzinfo is already set)")
+        utc_dt = order_dt.replace(tzinfo=timezone.utc)
+
+    local = utc_dt.astimezone(ZoneInfo(config.TIMEZONE_NAME))
```

- 인용 규칙: `DEP-DT-01`, `DEP-TZ-01`, `DEP-TZ-02`, `STY-01`, `BIZ-SET-01`, `BIZ-TIM-01`
- 시간대가 붙은 입력을 거부하는 원래 동작(`pytz.localize`의 ValueError)을 유지했다. 이전 실행에서 공격 에이전트가 이 회귀를 찾아 이력에 저장해 두었고, 이번 실행의 공격 에이전트가 그 이력을 먼저 검색해 다시 확인했다 (F4-3 재사용).
- **판정**: `behavior_preserved` 200개 중 불일치 0개, DEP·STY 0건, DeprecationWarning을 오류로 한 기존 테스트 1,145개 통과, 변경 범위 통과. `attack_regressions_preserved`는 공격이 제안한 회귀 입력이 없어 "해당 없음"이다.
- 수정 9회 + 공격 8회 호출, 12.8만 토큰, **$0.16, 232초**

### 5.3 S3 보안 조치: SQL 문자열 결합 (SR-2103)

```diff
-    sql = f"SELECT * FROM customers WHERE name = '{name}' ORDER BY id"
-    logger.debug("find_by_name: %s", sql)
-    return db.query_all(conn, sql)
+    sql = "SELECT * FROM customers WHERE name = ? ORDER BY id"
+    return db.query_all(conn, sql, (name,))
```

- 인용 규칙: `SEC-SQL-01`(파라미터 바인딩), `SEC-LOG-03`(SQL 전문 로그 제거)
- **판정**: SEC 0건, 공격 입력 4개 모두 무력화, 정상 입력 200개 중 불일치 0개, 기존 테스트 통과, 변경 범위 통과.
- **공격**: 에이전트가 "`name=None`이면 원본은 문자열 `'None'`을 찾고 수정본은 NULL과 비교한다"는 회귀를 보고했다. 그러나 확인 실행에서 입력 형식이 틀려 양쪽 모두 같은 TypeError가 났고, 미확인 처리됐다. 그래서 공격 이력에 저장되지 않았고 판정에도 영향이 없었다 (발견 확인 단계가 의도대로 동작한 사례).
- 수정 8회 + 공격 11회 호출, 11.0만 토큰, **$0.12, 165초**

세 시나리오 모두 PRD 9장의 "티켓 1건 15분 이내"를 만족한다.

---

## 6. E2E 배치 (F7-2)와 Ablation 준비 (F7-3)

### 6.1 진행 방식 (`src/sr_agent/e2e.py`)

1. 홀드아웃 결함 20건마다 작업본(`original/`·`work/`)에 결함을 심는다. 티켓은 운영 담당자 관점(`-A`) 1건씩이다.
2. 결함 때문에 실패하는 기존 테스트를 두 작업본에서 **숨긴다.** S1처럼 "테스트는 통과하는데 증상만 보고된" 상황에서 시작하게 하려는 것이다. 숨긴 테스트는 판정(`hidden_tests_pass`)에서만 쓴다.
3. 참조 구현 = 결함을 심기 전 원래 함수.
4. 워크플로를 그대로 돌리고, 끝난 티켓은 결과를 이어서 쓴다 (중단 후 재실행 가능).
5. 해결률, 모델 Top-3 적중, 에이전트 최종 위치 정답, 통과한 시도 차수, 토큰, 비용, 시간을 집계하고 주입 방식별로 나눠 본다.

### 6.2 결과 (`eval/results/e2e_full.md`)

| 지표 | 값 |
|---|---|
| **해결률** (사람 개입 없이 판정 통과) | **17/20 (85%)**, 목표 60% |
| 모델 Top-3 적중 | 85% (17/20) |
| 에이전트 최종 위치 정답 | **100%** (20/20). 모델 Top-3 밖이던 3건도 모두 맞힘 |
| 통과한 시도 차수 | 17건 모두 1회째 |
| 주입 방식별 해결률 | AST 11건 중 82%, LLM 9건 중 89% |
| 토큰 합계 / 평균 | 528만 / 26.4만 |
| 비용 합계 | $6.07 |
| 평균 소요 시간 | 351초 |

미해결 3건:

| 티켓 | 정답 함수 | 사유 |
|---|---|---|
| E2E-bee37ca8-A | `shipping.decide_return_fee_payer` | 재현 테스트 기대값이 업무 규칙과 달라 분석 3회 모두 실패 |
| E2E-eece0961-A | `tax.split_mixed_items` | 같은 사유 |
| E2E-2adc72a0-A | `review.can_write_review` | 2회째 시도 중 티켓 토큰 상한(80만) 초과 |

세 건 모두 위치는 맞혔다. 실패는 **"올바른 기대값을 정하는 단계"**와 **비용 상한**에서 났다.

### 6.3 Ablation 준비

같은 20건을 조건만 바꿔 돌리는 옵션을 넣었다. 실행은 5일차에 한다.

| 옵션 | 조건 | 결과 이름 |
|---|---|---|
| (없음) | 전체 기능 | `e2e_full` (완료) |
| `--no-predict` | 분석 에이전트에 `predict_fault_location`을 주지 않음 | `e2e_no_predict` |
| `--no-attack` | 공격 단계를 건너뜀 | `e2e_no_attack` |

예측 결과는 분석 전에 상태에 저장하므로, 분석이 실패한 건도 모델 정확도 집계에 들어간다.

---

## 7. 진행 과정과 주요 결정

| 순서 | 상황 | 결정 | 반영 |
|---|---|---|---|
| 1 | "결함 영역 밖 입력은 원본과 동일"의 영역을 정하기 어려움 | 정답 구현과 비교해, 원본도 틀린 입력 = 미달, 원본은 맞은 입력 = 회귀로 분류 | PRD v0.8, 7.2 |
| 2 | 공격 에이전트가 발견 직후 반복 상한에 걸려 결과를 잃음 | 루프의 마지막 턴은 도구를 막고 결과를 정리하게 함 | `agents/loop.py` |
| 3 | 통합 실행에서 수정 재시도가 수렴하지 않음 | 수정·분석 프롬프트 보완, 티켓 토큰 상한 50만 → 80만 | `config.TICKET_TOKEN_LIMIT` |
| 4 | S2에서 `utcnow`를 고쳤지만 `import pytz`가 남음 | 쓰지 않게 된 지원 종료 import도 위반으로 셈 | `judge.py`, 수정 프롬프트 |
| 5 | S3에서 공격 에이전트가 DB 함수를 실행하지 못함 | `$db` 표기를 도구 설명·프롬프트에 추가 | `attack.md`, `tools/attack.py` |
| 6 | S1 실행에서 재현 테스트 기대값이 틀려, 정답과 200개 일치하는 패치도 재시도 상한까지 감 | 재현 테스트가 정답 구현에서 통과해야 분석을 받음. 정답 코드는 숨김 | `graph.py`, `MAX_ANALYSIS_TRIES = 3` |
| 7 | S2에서 공격이 회귀(시간대가 붙은 입력: 원본 ValueError → 수정본 계산)를 찾았지만 판정은 통과 | 공격이 제안한 회귀 입력을 판정이 다시 실행 (`attack_regressions_preserved`) | `judge.py` |
| 8 | E2E 2건 시험: 분석이 티켓과 무관한 단언을 넣어 재현 검증 반복 실패, 보고한 재현 테스트 경로 불일치 | 프롬프트 보완, 경로는 실제 작성된 파일로 보정, 재분석 피드백에 실패한 단언 줄(값 제외) 포함 | `analysis.md`, `graph.py` |

6번과 7번은 같은 원칙에서 나왔다. **LLM은 입력과 후보를 제안하고, 맞고 틀림은 코드가 정한다.** 재현 테스트의 기대값과 공격의 회귀 주장을 모두 코드로 다시 확인한다.

---

## 8. 한계와 주의할 점

- **S1의 "1차 패치 미달 → 재시도" 장면이 자연스럽게 나오지 않는다.** S1은 첫 시도에 완전히 고쳐진다. 공격·판정의 미달 탐지는 일부러 불완전하게 만든 패치 3종(하한만 고침, 기준만 고침, 경계 회귀)을 판정이 모두 잡는 테스트(`tests/test_attack.py`)로만 보여 준다. PRD KPI "불완전 패치 10건"까지는 7건이 더 필요하다.
- **E2E 판정에는 Hypothesis 정답 비교가 빠져 있다.** 홀드아웃 결함마다 입력 생성 규칙을 만들지 않았기 때문이다. `reference_property`는 "미평가"이고, 대신 숨긴 기존 테스트와 정답 구현으로 검증한 재현 테스트로 판정한다.
- **재시도 경로가 E2E에서 검증되지 않았다.** 해결 17건이 모두 1회째에 통과해, 재시도 횟수를 늘릴지 판단할 근거가 아직 없다 (PRD 12장 미결).
- **기대값 단계가 가장 약하다.** 미해결 3건 중 2건이 재현 테스트 기대값 문제다. 업무 규칙 KB에 없는 동작이면 에이전트가 기대값을 정할 근거가 부족하다.
- **비용 편차가 크다.** 티켓당 11만~81만 토큰이다. 상한 80만을 넘긴 1건은 사람 검토로 넘어갔다.
- **재생 데이터는 한 번의 실행 결과다.** 실시간 모드는 실행마다 탐색 경로·턴 수·인용 이력이 달라질 수 있다.

---

## 9. 새로 생긴·바뀐 파일

```
app/
└── streamlit_app.py            # 화면 (사이드바, 진행 단계, 탭 4개, 승인·반려)

data/
├── scenarios.json              # S1~S3 판정 명세 추가 (진입 함수, 입력 생성 규칙, 정답 함수, 공격 입력)
└── replay/SR-2041|SR-2077|SR-2103/
    ├── state.json              # 재생용 최종 상태
    └── events.jsonl            # 재생용 실행 이벤트

src/sr_agent/
├── ui.py                       # 단계·KPI·승인·반려·티켓 등록, 재생, save-replay CLI
├── e2e.py                      # 홀드아웃 결함 준비(결함 심기, 테스트 숨기기), 집계
├── agents/prompts/attack.md    # 공격 에이전트 지시문
├── tools/
│   ├── attack.py               # run_diff 노출, property_attack, attack_memory
│   ├── _property_runner.py     # 원본·수정본·참조 구현 Hypothesis 비교 (subprocess)
│   └── checks.py               # check_deprecated, check_style, check_security
└── workflow/
    ├── attack.py               # 공격 단계, 발견 확인·저장
    ├── reference.py            # 참조 구현 불러오기
    ├── judge.py                # 판정 3유형
    └── graph.py                # 유형별 흐름, 공격 단계, Ablation 옵션(RunOptions)

eval/
├── run_e2e.py                  # E2E·Ablation 실행
└── results/e2e_full.{md,csv,json}, e2e_full_n2.*   # 20건 본 실행, 2건 시험 실행
```

---

## 10. 테스트 (에이전트 시스템 `tests/`, 322개 + 실제 API 3개)

4일차에 63개를 추가했다. 아래 개수는 테스트 함수 수이고, 파라미터로 늘어나는 경우가 있다.

| 파일 | 함수 | 확인하는 것 |
|---|---|---|
| `test_attack.py` | 12 | Hypothesis 비교·축소·시드 고정, 미달·회귀 분류, 발견 확인, 공격 이력 저장·검색, **S1 불완전 패치 3종을 판정이 잡음 + 정상 패치는 통과** |
| `test_judge_types.py` | 7 | **S2 통과 1 / 불통과 3** (동작 변경, `utcnow` 잔존, 타입 힌트 없음), **S3 통과 1 / 불통과 4** (원본, 따옴표 이스케이프만, SQL 로그 잔존, 정렬 변경), "0건" 범위, 지원 종료 import 잔존, 유형 라우팅 |
| `test_ui.py` | 11 | 단계·KPI 계산, 승인·반려 기록, 티켓 등록, 재생 저장·불러오기, 인용 ID 추출, AppTest 렌더링, 판정 실패 시 승인 비활성 |
| `test_e2e.py` | 6 | 결함당 티켓 1건, 실패 테스트 숨기기, 판정 명세 생성, 숨은 테스트 판정 통과·실패, 집계 |
| `test_workflow.py` | +6 | 확인된 발견의 저장·재시도 전달, 공격 입력에 정답 구현이 없음, 공격 거부 → 사람 검토, 틀린 기대값의 재현 테스트 거부, 재현 테스트 경로 보정·피드백에 값 숨김, 코드 표준화 유형의 분석 생략 |
| `test_agent_llm.py` | +1 | (`pytest -m llm`) 실제 공격 에이전트가 불완전 패치 A·B의 미달을 규칙 근거와 함께 찾음 |

---

## 11. 직접 실행하기

```powershell
.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "src"

streamlit run app/streamlit_app.py                     # 화면 (사이드바에서 재생·실시간 선택)
python -m sr_agent.run --ticket SR-2077 --restart      # CLI로 S2 실행 (약 4분, 약 $0.16)
python -m sr_agent.ui save-replay SR-2041              # 실행 결과를 재생 데이터로 저장
python -m eval.run_e2e --split holdout --limit 2       # E2E 2건 시험
python -m eval.run_e2e --split holdout                 # E2E 20건 (약 2시간, 약 $6)
pytest tests                                           # 322 passed (약 5분 30초)
```

---

## 12. 다음 단계 (5일차)

PRD 10장 5일차: 오전 E2E·Ablation 정리, 오후 KPI·발표 준비. 완료 기준은 **KPI·Ablation 결과표와 리허설 완료**다.

| 남은 일 | 내용 |
|---|---|
| Ablation (F7-3) | `--no-predict`, `--no-attack`으로 같은 20건 실행 (약 $12). 해결률·토큰·시간, 공격 유무별 판정 통과 후 숨은 회귀 누출 비교 |
| KPI 공격 탐지율 | 불완전 패치 10건으로 확대 (현재 S1 3건) |
| KPI 근거 인용률 | 패치 설명의 규칙 ID·이력 ID 포함 비율 집계 (E2E·시연 결과에서) |
| KPI 결과표 | PRD 2장 8개 지표를 한 표로 |
| 리허설 | 재생 모드로 S1 → S2 → S3 시연 |

| 남은 미결 사항 (PRD 12장) | 메모 |
|---|---|
| 현업 문제 수치와 E2E 해결률 목표의 근거 | 발표 자료에 필요 |
| fallback 모델 지정 여부 | 기본안(끔) 유지 중 |
| 재시도 횟수 상향 여부 | E2E에서 재시도로 통과한 사례가 없어 판단 근거 부족 |
| KPI "불완전 패치 10건" 작성 방법 | 위 KPI 공격 탐지율과 함께 |
