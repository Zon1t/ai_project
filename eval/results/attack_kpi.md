# 공격 에이전트 탐지율 (PRD 2장 KPI)

- 불완전 패치 10건 중 탐지 **10건 (100%)**, 목표 80% 이상
- 탐지 = 판정이 공격 에이전트가 낸 입력을 다시 실행해 정답 구현(표준화는 원본)과 다른 결과를 찾음
- 패치는 사람이 쓰고(data/attack_kpi/patches.json), 공격 전에 코드로 불완전함을 확인 (변경 범위, 기존 테스트 통과, 점검 입력에서 기준과 다름). 점검 입력은 공격 에이전트에 주지 않음

| 케이스 | 함수 | 결함 | 불완전 내용 | 결과 | 판정 재실행 |
|---|---|---|---|---|---|
| K01 | `discount.apply_coupon` | 미달 | 0원 하한(BIZ-CPN-02)만 넣고, 최소 주문 금액을 여전히 원래 주문 금액으로 판단(BIZ-CPN-01 미반영) | ✅ 탐지 | 공격 입력 5개 중 정답과 다름 5개 (미달 5, 회귀 0). 예(미달): discount.apply_coupon([100000, {"type": "FIXED", "value": 5000, "min_order": 100000}, 99999]) → 수정본 {"ok": true, "value": 94999}, 원본 {"ok": true, "value": 94999} |
| K02 | `discount.apply_coupon` | 미달 | 최소 주문 금액을 현재 금액으로 판단(BIZ-CPN-01)했지만 0원 하한(BIZ-CPN-02)이 없음 | ✅ 탐지 | 공격 입력 4개 중 정답과 다름 3개 (미달 3, 회귀 0). 예(미달): discount.apply_coupon([120000, {"type": "FIXED", "value": 10000, "min_order": 1}, 9990]) → 수정본 {"ok": true, "value": -10}, 원본 {"ok": true, "value": -10} |
| K03 | `discount.apply_coupon` | 회귀 | 두 규칙은 모두 고쳤지만 결제 금액을 10원 단위로 내림 (결함과 무관한 금액이 바뀜) | ✅ 탐지 | 공격 입력 4개 중 정답과 다름 4개 (미달 0, 회귀 4). 예(회귀): discount.apply_coupon([100003, {"type": "FIXED", "value": 1, "min_order": 1}]) → 수정본 {"ok": true, "value": 100000}, 원본 {"ok": true, "value": 100002} |
| K05 | `customer_dao.find_by_name` | 미달 | f-string은 그대로 두고 입력에서 작은따옴표만 지움 (O'Brien 같은 정상 이름을 못 찾음) | ✅ 탐지 | 공격 입력 3개 중 정답과 다름 3개 (미달 3, 회귀 0). 예(미달): customer_dao.find_by_name([{"$db": {"customers": [{"id": 1, "name": "Alice", "email": "a@x.com", "phone": "010-1111-2222", "grade": "BASIC"}, {"id": 2, "name": "O'Connor", "email": "o@x.…) → 수정본 {"ok": true, "value": []}, 원본 {"ok": false, "exception": "OperationalError: near \"Connor\": syntax error"} |
| K06 | `customer_dao.find_by_name` | 회귀 | 파라미터 바인딩으로 바꿨지만 = 대신 LIKE '%이름%'으로 바꿔 부분 일치도 돌려줌 | ✅ 탐지 | 공격 입력 6개 중 정답과 다름 6개 (미달 0, 회귀 6). 예(회귀): customer_dao.find_by_name([{"$db": {"customers": [{"name": "홍길동", "email": "hong@example.com", "phone": "010-1000-1000", "grade": "BASIC"}, {"name": "김홍길동", "email": "kimhong@example.com…) → 수정본 {"ok": true, "value": [{"id": 1, "name": "홍길동", "email": "hong@example.com", "phone": "010-1000-1000", "grade": "BASIC", "created_at": null}, {"id": 2, "name": …, 원본 {"ok": true, "value": [{"id": 1, "name": "홍길동", "email": "hong@example.com", "phone": "010-1000-1000", "grade": "BASIC", "created_at": null}]} |
| K07 | `customer_dao.find_by_name` | 회귀 | 파라미터 바인딩으로 바꾸면서 이름 앞뒤 공백을 지움 (공백이 다른 이름도 같은 사람으로 찾음) | ✅ 탐지 | 공격 입력 7개 중 정답과 다름 7개 (미달 1, 회귀 6). 예(회귀): customer_dao.find_by_name([{"$db": {"customers": [{"id": 1, "name": "Alice", "email": "a@x.com", "phone": "010-1111-1111", "grade": "BASIC"}, {"id": 2, "name": "Bob", "email": "b@x.com",…) → 수정본 {"ok": true, "value": [{"id": 1, "name": "Alice", "email": "a@x.com", "phone": "010-1111-1111", "grade": "BASIC", "created_at": null}]}, 원본 {"ok": true, "value": []} |
| K08 | `tax.calc_late_penalty` | 미달 | 지연 일수 0일만 0원으로 막고 음수 일수는 그대로 계산 | ✅ 탐지 | 공격 입력 1개 중 정답과 다름 1개 (미달 1, 회귀 0). 예(미달): tax.calc_late_penalty([100000, -1]) → 수정본 {"ok": true, "value": -20}, 원본 {"ok": true, "value": -20} |
| K09 | `tax.calc_simplified_vat` | 미달 | 부가가치율이 상한을 넘을 때만 오류로 막고, 하한보다 작을 때는 여전히 0원 | ✅ 탐지 | 공격 입력 4개 중 정답과 다름 4개 (미달 3, 회귀 1). 예(미달): tax.calc_simplified_vat([100000, 14]) → 수정본 {"ok": true, "value": 0}, 원본 {"ok": true, "value": 0} |
| K10 | `notification.should_retry` | 회귀 | 재시도할 수 없는 오류는 고쳤지만 최대 횟수 비교를 <에서 <=로 바꿔 한 번 더 재시도 | ✅ 탐지 | 공격 입력 1개 중 정답과 다름 1개 (미달 0, 회귀 1). 예(회귀): notification.should_retry(["TIMEOUT", 3]) → 수정본 {"ok": true, "value": true}, 원본 {"ok": true, "value": false} |
| K12 | `review.calc_average_rating` | 미달 | 계산식은 고쳤지만 반올림을 내장 round()로 바꿔 x.x5에서 half-up이 아니라 내림처럼 동작 | ✅ 탐지 | 공격 입력 3개 중 정답과 다름 3개 (미달 3, 회귀 0). 예(미달): review.calc_average_rating([[3, 3, 3, 4]]) → 수정본 {"ok": true, "value": 3.2}, 원본 {"ok": true, "value": 211.2} |

불완전 확인에서 뺀 케이스: K04 (기존 테스트에서 잡힘 (공격 없이도 판정이 막음): 통과 1144, 실패 1: ['tests/test_settlement.py::test_due_date_kst_month_boundary']), K11 (점검 입력에서 기준과 결과가 같음 (불완전하지 않음))
