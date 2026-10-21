# E2E 배치: no_attack (F7-2)

- 조건: 예측 도구 사용, 공격 단계 끔
- 홀드아웃 결함 20건, 티켓 1건씩 (결함 때문에 실패하는 기존 테스트는 숨기고 판정만 사용)
- 가격 가정(100만 토큰당): 입력 $1.25, 캐시 $0.125, 출력 $10.0

| 지표 | 값 |
|---|---|
| **해결률** (사람 개입 없이 판정 통과) | **19/20 (95%)** |
| 판정 통과 + 숨긴 테스트 통과 | 19/20 |
| 회귀 누출 (판정 통과 후 숨긴 테스트 실패) | 0건 |
| 근거 인용률 (마지막 패치 설명 본문에 규칙·이력 ID) | 100% (rule_ids·history_ids 필드 기준 100%) |
| 모델 Top-3 적중 | 85% |
| 에이전트 최종 위치 정답 | 95% |
| 평균 수정 시도 | 0.95 |
| 통과한 시도 차수 | 1회째 19건, 2회째 0건, 3회째 0건, 4회째 0건 |
| 토큰 합계 / 평균 | 3,014,988 / 150,749 |
| 비용 합계 | $3.6 |
| 평균 소요 시간 | 217.2초 |
| 공격 라운드 합계 | 0 |
| 확인된 발견을 낸 관점 (티켓 수) | 경계값 0, 타입·빈 값 0, 호출부 경로 0, 조합·순서 0, 성질 기반 0 |

| 주입 방식 | n | 해결률 |
|---|---|---|
| ast | 11 | 100% |
| llm | 9 | 89% |

## 티켓별

| 티켓 | 정답 함수 | 방식 | 결과 | 숨긴 테스트 | 모델 순위 | 에이전트 위치 | 시도 | 토큰 | 비용 | 시간 | 사유 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| E2E-00594a87-A | `coupon.create_coupon` | ast/RSD | ✅ | ✅ | 1 | ✅ | 1 | 158,256 | $0.1924 | 262.1 |  |
| E2E-0525b867-A | `seller.calc_commission_rate` | llm/LLM | ✅ | ✅ | 1 | ✅ | 1 | 119,593 | $0.168 | 227.7 |  |
| E2E-105ffb85-A | `tax.validate_invoice_number` | ast/CRP | ✅ | ✅ | 1 | ✅ | 1 | 105,405 | $0.134 | 167.8 |  |
| E2E-156b102c-A | `seller.calc_delay_rate` | ast/ARG | ✅ | ✅ | 1 | ✅ | 1 | 180,509 | $0.183 | 203.8 |  |
| E2E-262dcc8f-A | `seller.insert_seller` | ast/MCR | ✅ | ✅ | 1 | ✅ | 1 | 129,036 | $0.1607 | 175.2 |  |
| E2E-27202c7f-A | `review.calc_average_rating` | ast/AOR | ✅ | ✅ | 1 | ✅ | 1 | 126,984 | $0.1621 | 178.0 |  |
| E2E-29937977-A | `payment.mask_card_number` | ast/AOR | ✅ | ✅ | 1 | ✅ | 1 | 66,343 | $0.0927 | 113.0 |  |
| E2E-2adc72a0-A | `review.can_write_review` | ast/RSD | ✅ | ✅ | 1 | ✅ | 1 | 139,526 | $0.1538 | 176.7 |  |
| E2E-3b277643-A | `payment.process_payment` | ast/ARG | ✅ | ✅ | 2 | ✅ | 1 | 114,825 | $0.1818 | 194.2 |  |
| E2E-4783b928-A | `coupon.issue_coupon` | llm/LLM | ✅ | ✅ | 1 | ✅ | 1 | 188,451 | $0.2106 | 238.8 |  |
| E2E-6682b210-A | `order.get_items` | llm/LLM | ✅ | ✅ | 2 | ✅ | 1 | 124,679 | $0.1568 | 212.6 |  |
| E2E-7257e74d-A | `notification.add_ad_prefix` | ast/RSD | ✅ | ✅ | 3 | ✅ | 1 | 91,026 | $0.1211 | 155.4 |  |
| E2E-77a7bda9-A | `settlement.settle_order` | llm/LLM | ✅ | ✅ | - | ✅ | 1 | 213,010 | $0.2644 | 325.1 |  |
| E2E-879359a2-A | `settlement.get_settlement` | llm/LLM | ✅ | ✅ | - | ✅ | 1 | 175,172 | $0.1692 | 245.8 |  |
| E2E-8887648d-A | `tax.calc_simplified_vat` | llm/LLM | ✅ | ✅ | 1 | ✅ | 1 | 118,846 | $0.1556 | 209.9 |  |
| E2E-bee37ca8-A | `shipping.decide_return_fee_payer` | ast/RSD | ✅ | ✅ | 1 | ✅ | 1 | 112,341 | $0.1407 | 178.3 |  |
| E2E-c96c7dcc-A | `tax.calc_late_penalty` | llm/LLM | ✅ | ✅ | 1 | ✅ | 1 | 185,403 | $0.1898 | 226.3 |  |
| E2E-d7bb24d1-A | `notification.should_retry` | ast/CRP | ✅ | ✅ | 1 | ✅ | 1 | 87,163 | $0.1345 | 205.0 |  |
| E2E-eece0961-A | `tax.split_mixed_items` | llm/LLM | ✅ | ✅ | 1 | ✅ | 1 | 158,552 | $0.1698 | 203.3 |  |
| E2E-feb759f3-A | `db.query_one` | llm/LLM | ❌ human_review | - | - | ❌ coupon.validate_coupon | 0 | 419,868 | $0.4584 | 445.0 | 분석 실패: 재현 테스트의 기대값이 업무 규칙과 다름: 업무 규칙대로 올바르게 고친 코드에서도 실패하는 단언 |
