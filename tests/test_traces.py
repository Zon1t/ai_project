"""pytest 실패 출력 가공: 티켓용 traceback에 결함 코드가 새지 않는지, LLM 입력 발췌."""

from sr_agent.datagen.traces import failure_excerpt, first_traceback, to_traceback

EXCEPTION_TRACE = """\
def test_apply_coupon_fixed():
>       assert discount.apply_coupon(150000, fixed(10000)) == 140000
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests\\test_discount.py:56:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

order_amount = 150000
coupon = {'type': 'FIXED', 'value': 10000}

    def apply_coupon(order_amount, coupon, current_amount=None):
        \"\"\"쿠폰 1장을 적용한 뒤의 결제 금액.\"\"\"
        if current_amount is None:
            pass
        discount = calc_coupon_discount(current_amount, coupon)
>       return current_amount - discount
               ^^^^^^^^^^^^^^^^^^^^^^^^^
E       TypeError: unsupported operand type(s) for -: 'NoneType' and 'int'

src\\order_settlement\\discount.py:63: TypeError
"""

ASSERT_TRACE = """\
conn = <sqlite3.Connection object at 0x0000022DF1385C60>

    def test_partial_refund(conn):
>       assert payment.partial_refund(conn, pid, 30000) == 70000
E       assert 130000 == 70000

tests\\test_payment.py:80: AssertionError
"""

CHAINED_TRACE = """\
def test_due():
>       assert settlement.calc_due_date(dt) == date(2024, 3, 8)

tests\\test_settlement.py:40:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    def calc_due_date(order_dt=None):
>       month_end = date(local.year, local.month, last_day(local.year, local.month))

src\\order_settlement\\settlement.py:37: in calc_due_date
    ???
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    def helper(x):
>       raise ValueError("day is out of range for month")
E       ValueError: day is out of range for month

src\\order_settlement\\date_utils.py:12: ValueError
"""


def test_traceback_keeps_only_product_frames_without_function_source():
    tb = to_traceback(EXCEPTION_TRACE)
    assert tb == (
        "Traceback (most recent call last):\n"
        '  File "order_settlement/discount.py", line 63, in apply_coupon\n'
        "    return current_amount - discount\n"
        "TypeError: unsupported operand type(s) for -: 'NoneType' and 'int'"
    )
    assert "pass" not in tb and "test_apply_coupon_fixed" not in tb  # 결함 줄·테스트 코드는 빠진다


def test_traceback_multiple_frames_in_order():
    tb = to_traceback(CHAINED_TRACE)
    assert tb.splitlines()[1:] == [
        '  File "order_settlement/settlement.py", line 37, in calc_due_date',
        "    month_end = date(local.year, local.month, last_day(local.year, local.month))",
        '  File "order_settlement/date_utils.py", line 12, in helper',
        '    raise ValueError("day is out of range for month")',
        "ValueError: day is out of range for month",
    ]


def test_no_traceback_for_assertion_failure():
    assert to_traceback(ASSERT_TRACE) is None
    assert first_traceback([{"trace": ASSERT_TRACE}, {"trace": EXCEPTION_TRACE}]).endswith("'NoneType' and 'int'")
    assert first_traceback([{"trace": ASSERT_TRACE}]) is None


def test_failure_excerpt_hides_product_source_and_addresses():
    excerpt = failure_excerpt(EXCEPTION_TRACE)
    assert "discount.apply_coupon(150000, fixed(10000)) == 140000" in excerpt
    assert excerpt.endswith("오류: TypeError: unsupported operand type(s) for -: 'NoneType' and 'int'")
    assert "pass" not in excerpt and "current_amount - discount" not in excerpt

    excerpt = failure_excerpt(ASSERT_TRACE)
    assert "0x0000" not in excerpt and "AssertionError" not in excerpt
    assert "E       assert 130000 == 70000" in excerpt
