# E2E 배치: full (F7-2)

- 조건: 예측 도구 사용, 공격 단계 사용
- 홀드아웃 결함 20건, 티켓 1건씩 (결함 때문에 실패하는 기존 테스트는 숨기고 판정만 사용)
- 가격 가정(100만 토큰당): 입력 $1.25, 캐시 $0.125, 출력 $10.0

| 지표 | 값 |
|---|---|
| **해결률** (사람 개입 없이 판정 통과) | **17/20 (85%)** |
| 모델 Top-3 적중 | 85% |
| 에이전트 최종 위치 정답 | 100% |
| 평균 수정 시도 | 0.95 |
| 통과한 시도 차수 | 1회째 17건, 2회째 0건, 3회째 0건, 4회째 0건 |
| 토큰 합계 / 평균 | 5,280,442 / 264,022 |
| 비용 합계 | $6.07 |
| 평균 소요 시간 | 351.4초 |

| 주입 방식 | n | 해결률 |
|---|---|---|
| ast | 11 | 82% |
| llm | 9 | 89% |

## 티켓별

| 티켓 | 정답 함수 | 방식 | 결과 | 모델 순위 | 에이전트 위치 | 시도 | 토큰 | 비용 | 시간 | 사유 |
|---|---|---|---|---|---|---|---|---|---|---|
| E2E-00594a87-A | `coupon.create_coupon` | ast/RSD | ✅ | 1 | ✅ | 1 | 227,103 | $0.2272 | - |  |
| E2E-0525b867-A | `seller.calc_commission_rate` | llm/LLM | ✅ | 1 | ✅ | 1 | 242,359 | $0.3069 | - |  |
| E2E-105ffb85-A | `tax.validate_invoice_number` | ast/CRP | ✅ | 1 | ✅ | 1 | 116,560 | $0.1859 | 258.8 |  |
| E2E-156b102c-A | `seller.calc_delay_rate` | ast/ARG | ✅ | 1 | ✅ | 1 | 213,379 | $0.226 | 284.1 |  |
| E2E-262dcc8f-A | `seller.insert_seller` | ast/MCR | ✅ | 1 | ✅ | 1 | 262,747 | $0.2845 | 348.2 |  |
| E2E-27202c7f-A | `review.calc_average_rating` | ast/AOR | ✅ | 1 | ✅ | 1 | 129,890 | $0.185 | 209.6 |  |
| E2E-29937977-A | `payment.mask_card_number` | ast/AOR | ✅ | 1 | ✅ | 1 | 109,700 | $0.1991 | 231.1 |  |
| E2E-2adc72a0-A | `review.can_write_review` | ast/RSD | ❌ human_review | 1 | ✅ | 2 | 807,416 | $0.8476 | 919.4 | BudgetExceeded: 티켓 토큰 상한 초과: 807416/800000 |
| E2E-3b277643-A | `payment.process_payment` | ast/ARG | ✅ | 2 | ✅ | 1 | 234,215 | $0.2928 | 340.6 |  |
| E2E-4783b928-A | `coupon.issue_coupon` | llm/LLM | ✅ | 1 | ✅ | 1 | 279,428 | $0.3269 | 402.3 |  |
| E2E-6682b210-A | `order.get_items` | llm/LLM | ✅ | 2 | ✅ | 1 | 171,944 | $0.2482 | 316.6 |  |
| E2E-7257e74d-A | `notification.add_ad_prefix` | ast/RSD | ✅ | 3 | ✅ | 1 | 125,405 | $0.178 | 224.8 |  |
| E2E-77a7bda9-A | `settlement.settle_order` | llm/LLM | ✅ | - | ✅ | 1 | 360,214 | $0.3409 | 328.9 |  |
| E2E-879359a2-A | `settlement.get_settlement` | llm/LLM | ✅ | - | ✅ | 1 | 244,538 | $0.2309 | 240.6 |  |
| E2E-8887648d-A | `tax.calc_simplified_vat` | llm/LLM | ✅ | 1 | ✅ | 1 | 189,207 | $0.2339 | 293.4 |  |
| E2E-bee37ca8-A | `shipping.decide_return_fee_payer` | ast/RSD | ❌ human_review | 1 | ✅ | 0 | 346,156 | $0.3812 | 366.4 | 분석 실패: 재현 테스트의 기대값이 업무 규칙과 다름: 업무 규칙대로 올바르게 고친 코드에서도 실패하는 단언 |
| E2E-c96c7dcc-A | `tax.calc_late_penalty` | llm/LLM | ✅ | 1 | ✅ | 1 | 208,525 | $0.25 | 306.7 |  |
| E2E-d7bb24d1-A | `notification.should_retry` | ast/CRP | ✅ | 1 | ✅ | 1 | 206,745 | $0.2144 | 241.8 |  |
| E2E-eece0961-A | `tax.split_mixed_items` | llm/LLM | ❌ human_review | 1 | ✅ | 0 | 500,097 | $0.6421 | 702.8 | 분석 실패: 재현 테스트의 기대값이 업무 규칙과 다름: 업무 규칙대로 올바르게 고친 코드에서도 실패하는 단언 |
| E2E-feb759f3-A | `db.query_one` | llm/LLM | ✅ | - | ✅ | 1 | 304,814 | $0.2724 | 309.5 |  |
