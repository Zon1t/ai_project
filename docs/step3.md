# Step 3 정리: RAG, 에이전트 기반, 분석·수정 에이전트와 S1 (F4-1 ~ F5-5, F7-1)

> 작성일: 2026-10-20 · 범위: PRD 10장 3일차 · 상태: 완료
>
> 확인 결과: 에이전트 시스템 테스트 259 passed (+ 실제 API 테스트 2 passed, `pytest -m llm`), ruff 통과.
> **S1(SR-2041)이 CLI에서 끝까지 동작**: 분석 → 재현 확인 → 수정 → 판정 통과 → 승인 대기, 139초, 약 $0.14

이 문서는 3일차에 만든 지식 저장소(RAG), 에이전트 공통 기반, 분석·수정 에이전트와 워크플로를 설명하고, S1 시나리오를 실제로 돌린 결과를 보여 준다.
앞 단계는 [step1.md](step1.md)(대상 프로젝트·실행 도구), [step2.md](step2.md)(결함 데이터·예측 모델)에 있다.

---

## 1. 한눈에 보기

### 1.1 무엇을 만들었나

2일차까지는 "부품"(대상 코드, 실행 도구, 결함 위치 예측 모델)을 만들었다. 3일차에는 이 부품을 써서 **티켓 하나를 처음부터 끝까지 처리하는 에이전트**를 만들었다.

```
SR 티켓 (data/scenarios.json)
   │
   ▼
[분석 에이전트]  gpt-5 · 도구: 위치 예측, 이력 검색, 규칙 검색, 코드 읽기, 호출 관계, 커버리지, 재현 테스트 작성·실행
   │  → AnalysisOutput: 결함 함수, 모델 순위, 판단 이유, 근거, 재현 테스트
   ▼
[재현 확인]  코드: 재현 테스트가 원본에서 실제로 실패하는가 (아니면 분석 1회 다시)
   │
   ▼
[수정 에이전트]  gpt-5 · 도구: 규칙 검색, 코드 읽기, 패치 적용, 테스트 실행
   │  → FixOutput: 패치 설명, 인용한 규칙 ID·이력 ID
   ▼
[판정 노드]  코드만 (LLM 없음): 재현 테스트 · 기존 테스트 · 변경 범위
   │  실패 → 실패 내용을 붙여 수정 재시도 (최대 3회) → 넘으면 사람 검토
   ▼
승인 대기  (승인·반려 화면은 4일차)
```

노드 사이에는 대화가 아니라 **구조화된 결과(pydantic 모델)**만 넘긴다. 진행 상태는 `runs/<ticket_id>/state.json`에 저장돼 중간에 끊겨도 이어서 실행할 수 있다.

### 1.2 결과 요약

| 항목 | 값 |
|---|---|
| 사내 규칙 KB | 40건 (보안 12 · 지원 종료 API 8 · 팀 스타일 6 · 업무 14) |
| 장애·조치 이력 KB | 1,057건 (학습 분할 결함만) |
| 에이전트 도구 | 11개 구현 (PRD 7.3의 15개 중. 나머지 4개는 4일차 공격·검사용) |
| S1 실행 | 승인 대기, 분석 11턴 + 수정 5턴, 139초, 토큰 10.3만(입력의 88%가 캐시), 약 $0.14 |
| 테스트 | 168 → **259개** (+ 실제 API 2개) |

### 1.3 PRD 기능별 결과물

| PRD ID | 내용 | 결과물 |
|---|---|---|
| F4-1 | 장애·조치 이력 KB, `search_history` | `src/sr_agent/rag/`, `tools/search.py` |
| F4-2 | 사내 규칙 KB, `search_rules` | `data/rules/rules.json`, `rag/`, `tools/search.py` |
| F5 공통, F7-1 | 에이전트 루프, 도구 등록·허용 목록, 실행 로그, 토큰 상한 | `src/sr_agent/agents/loop.py`, `tooling.py`, `context.py` |
| F5-1 | 분석 에이전트 | `agents/prompts/analysis.md`, `agents/specs.py` |
| F5-2 | 수정 에이전트 | `agents/prompts/fix.md`, `agents/specs.py` |
| F5-4 | 판정 노드 (결함 수정 유형) | `src/sr_agent/workflow/judge.py` |
| F5-5 | 재시도 3회, 사람 검토 전환 | `src/sr_agent/workflow/graph.py` |
| (실행) | CLI | `src/sr_agent/run.py` |

---

## 2. RAG: 사내 규칙과 장애·조치 이력 (F4-1, F4-2)

에이전트가 "근거"로 인용할 두 지식 저장소다. 임베딩은 2일차에 고른 bge-m3를 쓰고, 저장소는 Chroma(`data/chroma/`, git 제외)다.

### 2.1 사내 규칙 40건 (`data/rules/rules.json`)

규칙 ID는 `분류-주제-번호`다. 공개된 시큐어코딩 가이드·파이썬 공식 권고를 요약해 다시 쓴 것과 가상의 업무 규칙이다.

| 분류 | 건수 | 예 | 쓰이는 시나리오 |
|---|---|---|---|
| **SEC** 보안 | 12 | `SEC-SQL-01` SQL은 파라미터 바인딩, `SEC-LOG-02` 카드번호 로그 금지, `SEC-CRY-01` MD5 금지 | S3 |
| **DEP** 지원 종료 API | 8 | `DEP-DT-01` `utcnow` → `now(timezone.utc)`, `DEP-TZ-01` `pytz` → `zoneinfo` | S2 |
| **STY** 팀 스타일 | 6 | `STY-01` 공개 함수 타입 힌트, `STY-02` `# type:` 주석 금지, `STY-03` f-string, `STY-04` docstring, `STY-05` `print` 금지, `STY-06` 맨 `except` 금지 | S2 |
| **BIZ** 업무 | 14 | `BIZ-CPN-01` 복수 쿠폰의 최소 주문 금액은 직전 쿠폰 적용 후 금액 기준, `BIZ-CPN-02` 결제 금액 0원 하한, 정산 지급일, 환불 수수료 | S1 |

- 규칙마다 `bad_example`·`good_example`, 판정 때 쓸 **검사 방법**(`check`, 예: `ruff UP031`), **코드 패턴**(`keywords`, 정규식)이 있다.
- 업무 규칙의 기준값(환불 7일·30일, 배송비 5만 원 등)은 대상 코드의 실제 동작과 맞췄다. **S1 결함만 규칙과 다르다** — 규칙은 코드 주석이 아니라 이 KB에만 있어서, 에이전트가 검색으로 찾아야 한다.
- 팀 스타일 규칙 6건은 모두 ruff나 AST로 검사할 수 있는 것만 골랐다 (판정은 코드가 해야 하므로).

### 2.2 장애·조치 이력 1,057건

2일차의 **학습(train) 분할 결함만** 이력으로 넣었다 (평가·홀드아웃 결함이 들어가면 평가가 부풀려진다). 결함 1개가 이력 1건이다.

| 필드 | 내용 |
|---|---|
| `history_id` | `HIS-` + 결함 해시. 에이전트가 패치 설명에 인용한다 |
| `ticket` | 그 결함의 운영·QA 티켓 문장 (이걸 임베딩해 검색) |
| `method` | 결함이 있었던 함수 |
| `root_cause`, `fix_summary` | 원인과 조치. 결함 기록에서 코드로 만든다 (LLM 미사용) |

학습 외 결함이 하나라도 섞이면 색인을 만들지 않고, 실제 Chroma 색인을 열어 확인하는 테스트도 있다.

### 2.3 검색 방식과 실제 결과

- `search_history(query, top_k=3)`: 증상 문장과 비슷한 이력.
- `search_rules(code, top_k=5)`: **코드**를 넣으면 관련 규칙. 임베딩 유사도에 **코드 패턴이 맞으면 0.3을 더한다** (예: 코드에 `utcnow`가 있으면 `DEP-DT-01`이 확실히 위로). 규칙이 40건뿐이라 전부 점수를 매겨 다시 정렬한다.

시나리오 대상 함수 코드로 검색한 실제 상위 결과 (bge-m3):

| 입력 | 상위 결과 |
|---|---|
| S1 `apply_coupon` | **BIZ-CPN-02, BIZ-CPN-01**, STY-02, STY-01, DEP-TYP-02 |
| S2 `calc_due_date` | BIZ-SET-01, BIZ-TIM-01, **DEP-TZ-02, DEP-DT-01, STY-01**, (6위) DEP-TZ-01 |
| S3 `find_by_name` | **SEC-SQL-01**, SEC-LOG-03, STY-01, STY-02, DEP-TYP-01 |

S2는 `pytz` 규칙(DEP-TZ-01)이 6위라, 코드 표준화 유형은 `top_k=8`로 검색하게 했다 (`config.SEARCH_RULES_TOP_K`).
S1 티켓 문장으로 이력을 찾으면 상위 3건이 모두 쿠폰 결함이고, 그중 하나는 "할인액을 최초 주문 금액 기준으로 계산"한 S1과 비슷한 과거 사례다.

---

## 3. 에이전트 공통 기반 (F5 공통, F7-1)

### 3.1 루프를 직접 짠 이유와 동작

CLAUDE.md 규칙에 따라 에이전트 프레임워크(OpenAI Agents SDK, LangChain 등)를 쓰지 않고 루프를 직접 짰다 (`agents/loop.py`). 디버깅이 쉽고, 로그·예산·허용 목록을 한곳에서 통제할 수 있다.

```
반복 (최대 20회):
  responses.parse(지시문, 지금까지의 입력, 도구 정의, 최종 결과 형식)
  ├─ 응답에 function_call이 있으면 → 도구 실행 → 같은 call_id로 결과를 입력에 추가 → 다시
  └─ 없으면 → 최종 구조화 결과(pydantic) 반환
```

| 장치 | 내용 |
|---|---|
| 응답 확인 | `status: incomplete`이면 오류, 거부(refusal)면 해당 티켓을 사람 검토로 |
| 토큰 상한 | 티켓당 50만 토큰 (모든 에이전트 합계). 넘으면 즉시 멈추고 사람 검토로 |
| prompt caching | 지시문·도구 정의를 고정 텍스트로 앞에 두고 에이전트별 `prompt_cache_key`. S1에서 입력의 약 88%가 캐시로 처리됨 |
| fallback | OpenAI에는 서버 측 fallback이 없어 클라이언트에서 한다. 재시도 후에도 서버 오류·연결 오류·404·429면 다른 모델로 다시 요청. **기본은 꺼짐** (모델을 바꾸려면 사용자 결정 필요) |
| 실행 로그 | `runs/<run_id>.jsonl`에 LLM 호출(토큰: 입력·캐시·출력·추론, 소요 시간, 누적 예산)과 도구 호출(입력·출력 요약, 오류 여부)을 한 줄씩 |

> 처음 작업 지시는 Claude SDK 용어(tool runner, `@beta_tool`, 서버 측 fallback)로 되어 있었다. 이 프로젝트는 OpenAI를 쓰므로 같은 목적의 OpenAI 방식으로 옮겼다.

### 3.2 도구 등록과 에이전트별 허용 목록

도구는 strict 스키마(`strict: True`, `additionalProperties: False`)로 등록한다. 작업본(`ws`)은 실행할 때 붙여서 LLM에는 PRD 7.3의 인자만 보인다.
허용 목록 밖의 도구를 부르거나 인자가 틀리면 **실행하지 않고 오류를 결과로 돌려준다** (모델이 읽고 고칠 수 있게).

| 도구 | 하는 일 | 분석 | 수정 | 공격 | 상태 |
|---|---|---|---|---|---|
| `predict_fault_location` | 결함 위치 Top-k + SHAP 근거 | O | | | 2일차 |
| `search_history` | 비슷한 과거 장애 | O | | | 3-1 |
| `search_rules` | 관련 사내 규칙 | **O** | O | | 3-1 (분석 허용은 8장 참고) |
| `read_method` | 작업본 함수 소스 + 위치 | O | O | O | 3-2 |
| `get_callers` | 이 함수를 부르는 함수 (정적 분석) | O | | O | 3-3 |
| `get_coverage` | 이 함수를 지나는 기존 테스트 (coverage.py) | O | | O | 3-3 |
| `write_repro_test` | `repro_tests/`에만 재현 테스트 작성 | O | | | 3-3 |
| `apply_patch` | 대상 함수 하나만 교체 + 같은 파일 import 추가·삭제 | | O | | 3-3 |
| `run_tests` | 원본 또는 작업본에서 테스트 실행 | O | O | | 1일차 |
| `run_diff`, `property_attack`, `attack_memory` | 공격 | | | O | 4일차 |
| `check_deprecated`, `check_security`, `check_style` | 검사 | | O | | 4일차 |

`apply_patch`의 안전장치: 함수 이름·인자·기본값은 바꿀 수 없다(타입 힌트 추가는 허용), 다른 함수·모듈 최상단 코드는 바꿀 수 없다, 다른 코드가 아직 쓰는 import는 지울 수 없다, 컴파일에 실패하면 파일을 바꾸지 않는다.
`get_coverage`는 전체 테스트를 테스트별 기록으로 한 번 돌리고(약 8초), 같은 작업본 상태에서는 결과를 다시 쓴다(0.06초).

---

## 4. 워크플로와 판정 (F5-1, F5-2, F5-4, F5-5)

### 4.1 노드별 역할

| 노드 | 누가 | 입력 | 출력 |
|---|---|---|---|
| 분석 | gpt-5 (추론 high) | 티켓 | `AnalysisOutput`: 결함 함수, 모델 Top-3와 그 함수의 순위, 판단 이유, 모델과 다른 이유, 근거 목록, 인용 이력, 재현 테스트 경로 |
| 재현 확인 | 코드 | 재현 테스트 경로 | 원본에서 단언 실패로 실패하는지. 아니면 이유를 붙여 분석 1회 다시 |
| 수정 | gpt-5 (추론 high) | 티켓 + 분석 결과(JSON) + 이전 시도의 실패 내용 | `FixOutput`: 패치 설명, 인용 규칙 ID, 인용 이력 ID |
| 판정 | 코드 | 작업본, 결함 함수, 재현 테스트 | 조건별 통과 여부 |

**예측은 참고, 판단은 에이전트**(PRD v0.6): 분석 에이전트는 예측 모델의 Top-3를 출발점으로 삼되, 호출 관계·이력·재현 테스트로 최종 위치를 정한다. 모델 1위와 다르게 고르면 그 이유를 써야 한다.

### 4.2 판정 조건 (결함 수정 유형, 코드로만)

| 조건 | 확인 방법 |
|---|---|
| 재현 테스트가 원본에서 실패 | `run_tests(original)`, 테스트 오류(import 실패 등)는 실패로 치지 않음 |
| 재현 테스트가 수정본에서 통과 | `run_tests(patched)` |
| 기존 테스트 모두 통과 | 대상 프로젝트 테스트 1,145개 |
| 변경 범위 | 대상 함수가 있는 파일만 바뀌고, 그 안에서도 대상 함수와 import 외에는 그대로. 테스트 파일·다른 파일·새 파일은 위반 |
| 결함 영역 밖 입력 동일 | **미평가** — 공격 단계(4일차)의 Hypothesis 입력으로 평가 |

판정 파일이 LLM을 import하지 않는지도 테스트로 확인한다 (CLAUDE.md 규칙 4).

### 4.3 재시도와 사람 검토

- 판정 실패 → 실패한 조건과 이전 diff를 붙여 수정 에이전트를 다시 부른다. 첫 시도 + 재시도 3회.
- 다음 경우 상태를 `human_review`로 바꾸고 멈춘다: 재시도 상한 초과, 재현 테스트를 끝내 만들지 못함, 모델 거부, 토큰 상한 초과, 에이전트가 20회 안에 결론을 못 냄.
- 판정을 통과하면 `approval_pending`(승인 대기)에서 멈춘다. 승인·반려는 4일차 화면에서 한다.

---

## 5. S1 실행 결과

```powershell
$env:PYTHONPATH = "src"
python -m sr_agent.run --ticket SR-2041 --restart
```

티켓: "10만 원 이상 주문에 쿠폰을 두 장 쓰면 정산 금액이 음수로 찍힙니다."

### 5.1 분석 (11턴, 106초)

- 예측 Top-3: `settlement.settle_order`, **`discount.apply_coupon`**, `discount.calc_coupon_discount`
- 이력 검색, 코드 읽기, 호출 관계(`apply_coupon ← apply_coupons ← calc_final_amount ← settle_order`), 커버리지, 규칙 검색(`BIZ-CPN-01·02`)을 거쳐 **모델 2위 `apply_coupon`을 결함 위치로 확정**했다.
- 모델 1위를 고르지 않은 이유: "`settle_order`는 음수 정산 금액이 드러나는 증상 지점일 뿐, 음수를 만드는 계산 오류는 `apply_coupon`에서 발생한다."
- 근거 14개: 코드 위치, 호출 관계, 커버리지, 규칙 2건, 이력 2건(`HIS-f45ea821`, `HIS-dc2217ee`), 재현 테스트 결과.

에이전트가 쓴 재현 테스트 (원본에서 `-20000`이 나와 실패):

```python
from order_settlement import discount


def test_two_coupons_should_not_make_amount_negative_and_second_may_become_inapplicable():
    # BIZ-CPN-01: 복수 쿠폰의 최소 주문 금액은 직전 쿠폰까지 적용된 금액으로 판단한다.
    # BIZ-CPN-02: 쿠폰 적용 후 결제 금액은 0원 아래로 내려가지 않는다(하한 0원).
    order_amount = 100_000
    coupons = [
        {"type": "FIXED", "value": 70_000, "min_order": 100_000},
        {"type": "FIXED", "value": 50_000, "min_order": 100_000},
    ]
    final_amount = discount.apply_coupons(order_amount, coupons)
    assert final_amount >= 0, "최종 금액이 음수로 내려가면 안 됩니다 (티켓 SR-2041)"
    assert final_amount == 30_000  # BIZ-CPN-01, BIZ-CPN-02
```

### 5.2 수정 (5턴, 28초)

```diff
-    if not is_coupon_applicable(order_amount, coupon):
+    # BIZ-CPN-01: 복수 쿠폰의 최소 주문 금액은 직전 쿠폰까지 적용된 금액으로 판단한다.
+    if not is_coupon_applicable(current_amount, coupon):
         return current_amount
     discount = calc_coupon_discount(current_amount, coupon)
-    return current_amount - discount
+    # BIZ-CPN-02: 할인 적용 후 결제 금액은 0원 아래로 내려가지 않는다(하한 0원).
+    return max(0, current_amount - discount)
```

인용: 규칙 `BIZ-CPN-01`, `BIZ-CPN-02` / 이력 `HIS-f45ea821`, `HIS-dc2217ee`

### 5.3 판정 (코드, 약 5초)

| 조건 | 결과 |
|---|---|
| 재현 테스트 원본 실패 | O |
| 재현 테스트 수정본 통과 | O |
| 기존 테스트 1,145개 통과 | O |
| 변경 범위 (대상 함수만) | O |
| 결함 영역 밖 입력 동일 | - (4일차) |

### 5.4 시간과 비용

| 에이전트 | 턴 | 입력 토큰 (캐시) | 출력 토큰 | 비용 |
|---|---|---|---|---|
| 분석 | 11 | 76,440 (68,736) | 9,510 | 약 $0.11 |
| 수정 | 5 | 14,556 (11,904) | 2,178 | 약 $0.03 |
| 합계 | 16 | | | **약 $0.14**, 139초 |

비용은 gpt-5 단가(입력 100만 토큰당 $1.25, 캐시 $0.125, 출력 $10)로 계산한 추정치다. PRD 9장의 "티켓 1건 15분 이내"를 충분히 만족한다.

---

## 6. 진행 과정과 주요 결정

S1은 세 번 실행해서 완성했다. 실패에서 찾은 문제를 고친 과정이 설계의 근거다.

| 순서 | 상황 | 결정 | 반영 |
|---|---|---|---|
| 1 | 작업 지시가 Claude SDK 용어로 되어 있음 | 같은 목적의 OpenAI 방식으로 옮김 (직접 짠 루프, 클라이언트 측 fallback) | 3장, PRD 7.3 구현 규약 |
| 2 | 시연에 필요한 팀 스타일 규칙 | 코드로 검사 가능한 6건 확정 | PRD 12장 |
| 3 | S2 코드로 규칙 검색 시 `pytz` 규칙이 6위 | 코드 표준화 유형은 `top_k=8` | `config.SEARCH_RULES_TOP_K` |
| 4 | fallback 모델 지정 여부 | 기본 꺼짐 유지. 장애 시 사람 검토, 시연은 결과 캐시 재생(PRD 9장)으로 대비 | `config.MODEL_FALLBACK = None` |
| 5 | **S1 1차 실행**: 분석 에이전트가 DB가 필요한 함수(`settle_order`)로 재현하려고 주문·DB 함수를 하나씩 읽다가 반복 상한 20회 초과 | 재현은 "DB 없이 부를 수 있는 가장 작은 함수"로, 원인을 정하면 탐색 중단, 도구는 한 번에 여러 개 호출 | 분석 프롬프트 |
| 6 | **S1 2차 실행**: 판정은 통과했지만 재현 테스트가 "결제 금액 0원"을 기대 (규칙상 정답은 30,000원). 수정 에이전트가 재현 테스트와 충돌하지 않으려고 `BIZ-CPN-01`을 일부러 적용하지 않음 | 분석 에이전트에도 `search_rules` 허용, 재현 테스트 기대값은 업무 규칙으로 정하고 규칙 ID를 주석에 적게 함 | PRD v0.7 (7.3, F5-1) |
| 7 | 2차 실행의 설명이 영어로 나옴 | "설명은 한국어로" 지시 추가 | 두 프롬프트 |
| 8 | **S1 3차 실행** | 두 규칙 모두 반영, 판정 통과, 139초·$0.14 | 5장 |

6번이 가장 중요한 교훈이다. **재현 테스트는 수정 에이전트가 고칠 수 없는 "정답"**이라서, 기대값이 틀리면 이후 단계가 아무리 잘해도 결함이 덜 고쳐진다. 그래서 기대값을 업무 규칙에 묶었다.

---

## 7. 한계와 주의할 점

- **LLM 결과는 실행마다 다를 수 있다.** 같은 티켓도 탐색 경로·턴 수·재현 테스트가 달라진다. 시연은 PRD 9장대로 결과 캐시를 재생하는 대비가 필요하다.
- **결함 영역 밖 입력 동일성은 아직 평가하지 않는다.** 지금 판정은 "재현 테스트와 기존 테스트"까지만 본다. 공격 단계가 붙어야 PRD 7.2의 판정 조건이 완성된다.
- **S1이 첫 시도에 완전히 고쳐진다.** PRD S1 기대 결과의 "1차 패치는 공격 에이전트가 경계값 미달을 찾아 재시도"가 자연스럽게 일어나지 않을 수 있다. 공격 에이전트의 미달 탐지는 KPI "불완전 패치 10건"으로 따로 보여 주는 편이 확실하다.
- **S1과 비슷한 과거 이력이 학습 분할에 있다.** 시연 함수 결함을 학습에만 둔다는 규칙에 따른 것이고 현실적인 "과거 유사 장애"이지만, 시연에서는 이 점을 밝히는 게 좋다.
- **처음 실행할 때 준비가 필요하다.** RAG 색인 생성(약 5분), 첫 예측 호출 때 bge-m3 로딩(약 21초), 첫 커버리지 측정(약 8초).
- **코드 표준화(S2)·보안(S3) 유형은 아직 실행할 수 없다.** 판정 노드와 검사 도구가 4일차에 추가된다.

---

## 8. 새로 생긴 파일

```
data/
├── rules/rules.json            # 사내 규칙 40건
└── scenarios.json              # 시연 티켓 S1~S3 (SR-2041, SR-2077, SR-2103)

src/sr_agent/
├── config.py                   # RAG·에이전트·판정 설정값 추가
├── run.py                      # CLI: python -m sr_agent.run --ticket SR-2041
├── rag/
│   ├── kb.py                   # 규칙 불러오기, 이력 문서(학습 분할만)
│   └── index.py                # Chroma 색인 생성·검색 (python -m sr_agent.rag.index)
├── agents/
│   ├── loop.py                 # 에이전트 루프 (도구 호출, 상한, 거부, fallback, 로그)
│   ├── tooling.py              # 도구 등록, 에이전트별 허용 목록
│   ├── context.py              # 실행 ID, 로그, 토큰 예산, 작업본
│   ├── specs.py                # 분석·수정 에이전트 정의와 입력 만들기
│   └── prompts/                # analysis.md, fix.md
├── workflow/
│   ├── state.py                # Ticket, AnalysisOutput, FixOutput, JudgeResult, TicketState
│   ├── judge.py                # 판정 노드 (코드만)
│   └── graph.py                # 분석 → 재현 확인 → 수정 → 판정, 재시도, 저장·재개
└── tools/
    ├── search.py               # search_history, search_rules
    ├── code.py                 # read_method, get_callers, apply_patch
    └── testing.py              # write_repro_test, get_coverage

runs/ (git 제외)
├── <ticket_id>/state.json      # 티켓 진행 상태
├── <ticket_id>/repro_tests/    # 에이전트가 쓴 재현 테스트
└── <run_id>.jsonl              # 실행 로그
```

---

## 9. 테스트 (에이전트 시스템 `tests/`, 259개 + 실제 API 2개)

3일차에 91개를 추가했다. LLM은 가짜 클라이언트로 대체해 비용 없이 돈다.

| 파일 | 개수 | 확인하는 것 |
|---|---|---|
| `test_rag.py` | 30 | 규칙 40건 형식, 시나리오 코드의 패턴 일치, S1 규칙이 대상 코드에 없음, **이력·실제 색인에 평가·홀드아웃 결함 없음**, 패턴 가산점 순위, 도구 입력 가드 |
| `test_agent_loop.py` | 13 | 도구 호출 왕복(`call_id`), 요청이 API 규칙을 따름(strict, effort, 캐시 키, temperature 없음), 허용 밖 도구·잘못된 인자, 반복 상한, 토큰 상한, 거부·incomplete, fallback |
| `test_code_tools.py` | 23 | `read_method`, `get_callers`(모듈·from import·실제 S1 호출 체인), `apply_patch`(시그니처 고정, 거부 시 파일 그대로, import 추가·삭제, 클래스 메서드 들여쓰기) |
| `test_testing_tools.py` | 9 | 재현 테스트 작성·거부, 커버리지 결과와 캐시 |
| `test_judge.py` | 8 | **통과해야 하는 패치 1건, 통과하면 안 되는 패치 4종**(패치 없음, 결함을 못 드러내는 재현 테스트, 기존 테스트 깨짐, 다른 함수 수정), 변경 범위, 판정에 LLM 없음 |
| `test_workflow.py` | 8 | 정상 흐름, 노드 입력이 구조화 결과뿐, 재현 실패 시 분석 재시도, 재시도 상한 → 사람 검토, 재시도 성공, 저장된 상태에서 이어서 실행 |
| `test_agent_llm.py` | 2 | (`pytest -m llm`, 비용 발생) 실제 gpt-5 + `read_method` 최소 호출, 없는 모델 → fallback 전환 |

판정·워크플로 테스트는 결함 하나가 든 작은 가짜 대상 프로젝트(`tests/conftest.py`의 `bug_target`)를 쓴다.

---

## 10. 직접 실행하기

```powershell
.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "src"

python -m sr_agent.rag.index                       # RAG 색인 생성 (처음 한 번, 약 5분)
python -m sr_agent.run --ticket SR-2041 --restart  # S1 실행 (약 2~3분, 약 $0.15)
pytest tests                                       # 259 passed (약 2분 20초)
pytest -m llm                                      # 실제 API 테스트 (비용 소량)
```

- 실행 중 진행 단계가 출력되고, 끝나면 분석 결과·근거·diff·판정이 요약된다.
- 같은 명령을 `--restart` 없이 다시 실행하면 저장된 상태에서 이어서 한다 (이미 끝난 티켓은 결과만 보여 줌).
- 실행 로그는 `runs/<run_id>.jsonl`, 상태는 `runs/SR-2041/state.json`에 있다.

---

## 11. 다음 단계 (4일차)

PRD 10장 4일차: 공격 에이전트, 코드 표준화·보안 판정, Streamlit 화면, E2E 배치. 완료 기준은 **S1~S3가 화면에서 동작**하는 것이다.

| 남은 일 | 내용 |
|---|---|
| 공격 에이전트 (F5-3) | `run_diff`·`property_attack`·`attack_memory`로 수정 미달과 회귀를 찾고, 결함 영역 밖 입력 동일성 판정을 채움 |
| 검사 도구 | `check_deprecated`, `check_security`, `check_style` → S2·S3 판정 |
| 화면 (F6) | 진행 단계, 결함 위치(모델 예측과 에이전트 판단을 나란히), 근거, diff, 공격 로그, 승인·반려 |
| E2E 배치 (F7-2) | 홀드아웃 20건, 모델 Top-3 정확도와 에이전트 최종 위치 정확도를 따로 집계, 통과한 재시도 차수 기록 |

| 넘겨받은 미결 사항 (PRD 12장) | 필요한 시점 |
|---|---|
| 결함 수정 판정의 "결함 영역 밖" 입력 범위 (홀드아웃 20건에 미리 정의) | 공격·판정 |
| S2 "출력 완전 동일" 비교 규칙 (naive/aware datetime, DST, 현재 시각 고정) | S2 판정 |
| 재시도 횟수 상향 여부 (E2E에서 통과 차수를 보고 결정) | E2E |
| KPI "불완전 패치 10건" 작성 방법 | 공격 에이전트 평가 |
