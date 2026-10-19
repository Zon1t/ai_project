"""실제 대상 프로젝트(target/)로 F1-3·F1-4와 S1~S3 시연 재료를 확인한다."""

from sr_agent import config
from sr_agent.tools.execution import run_diff, run_tests
from sr_agent.tools.workspace import compare_hashes, hash_tree, prepare_workspace, verify_original_intact

from .conftest import replace_in, write

CPN = {"type": "FIXED", "value": 10000, "min_order": 100000}


def test_original_tests_pass_and_target_unchanged(tmp_path):
    before = hash_tree(config.TARGET_ROOT)
    ws = prepare_workspace("SR-0000", runs_dir=tmp_path)

    result = run_tests(ws, "original")
    assert result["failed"] == []
    assert result["passed"] >= 150

    assert compare_hashes(before, hash_tree(config.TARGET_ROOT))["unchanged"] is True
    assert verify_original_intact(ws)["unchanged"] is True


def test_s1_repro_fails_on_original_and_fix_passes(tmp_path):
    ws = prepare_workspace("SR-2041", runs_dir=tmp_path)
    write(ws.repro_tests / "test_sr2041.py",
          "from order_settlement import discount\n\n\n"
          "def test_two_coupons_at_minimum():\n"
          f"    assert discount.apply_coupons(100000, [{CPN!r}, {CPN!r}]) == 90000\n")
    original = run_tests(ws, "original", ["repro_tests/test_sr2041.py"])
    assert [f["test"] for f in original["failed"]] == ["repro_tests/test_sr2041.py::test_two_coupons_at_minimum"]

    discount_py = ws.work / "src/order_settlement/discount.py"
    replace_in(discount_py, "is_coupon_applicable(order_amount, coupon)",
               "is_coupon_applicable(current_amount, coupon)")
    replace_in(discount_py, "return current_amount - discount", "return max(current_amount - discount, 0)")
    patched = run_tests(ws, "patched")
    assert patched["failed"] == []

    diff = run_diff(ws, "discount.apply_coupons", [[100000, [CPN, CPN]], [300000, [CPN, CPN]]])
    assert [d["same"] for d in diff] == [False, True]
    assert diff[0]["original"]["value"] == 80000 and diff[0]["patched"]["value"] == 90000


def test_s2_due_date_with_datetime_input(tmp_path):
    ws = prepare_workspace("SR-2077", runs_dir=tmp_path)
    result = run_diff(ws, "settlement.calc_due_date", [[{"$datetime": "2024-01-31T16:00:00"}]])
    assert result[0]["original"] == {"ok": True, "value": {"$date": "2024-03-08"}}


def test_s3_injection_detected_by_run_diff(tmp_path):
    ws = prepare_workspace("SR-2103", runs_dir=tmp_path)
    db = {"$db": {"customers": [
        {"name": "김민수", "email": "a@example.com", "phone": "010-1111-1111"},
        {"name": "이서연", "email": "b@example.com", "phone": "010-2222-2222"},
    ]}}
    inputs = [[db, "이서연"], [db, "' OR '1'='1"]]

    before = run_diff(ws, "customer_dao.find_by_name", inputs)
    assert len(before[1]["original"]["value"]) == 2  # 인젝션으로 전체 조회

    replace_in(ws.work / "src/order_settlement/customer_dao.py",
               "sql = f\"SELECT * FROM customers WHERE name = '{name}' ORDER BY id\"\n"
               "    logger.debug(\"find_by_name: %s\", sql)\n"
               "    return db.query_all(conn, sql)",
               "return db.query_all(conn, \"SELECT * FROM customers WHERE name = ? ORDER BY id\", (name,))")
    after = run_diff(ws, "customer_dao.find_by_name", inputs)
    assert after[0]["same"] is True
    assert after[1]["same"] is False and after[1]["patched"]["value"] == []
