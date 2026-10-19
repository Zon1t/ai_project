import pytest

from sr_agent.tools.workspace import (
    WorkspaceError,
    compare_hashes,
    hash_tree,
    open_workspace,
    prepare_workspace,
    resolve_in_workspace,
    verify_original_intact,
)

from .conftest import replace_in, write


def test_prepare_creates_two_identical_copies(ws, mini_target):
    assert ws.original.is_dir() and ws.work.is_dir() and ws.repro_tests.is_dir()
    source = hash_tree(mini_target)
    assert source
    assert hash_tree(ws.original) == source
    assert hash_tree(ws.work) == source


def test_prepare_skips_cache_files(mini_target, tmp_path):
    write(mini_target / "src" / "order_settlement" / "__pycache__" / "calc.cpython-312.pyc", "x")
    ws = prepare_workspace("SR-0002", source=mini_target, runs_dir=tmp_path / "runs")
    assert not list(ws.work.rglob("__pycache__"))
    assert "src/order_settlement/__pycache__/calc.cpython-312.pyc" not in hash_tree(mini_target)


@pytest.mark.parametrize("bad", ["", "../evil", "a/b", "a\\b", "..", "-x", "SR 1", "x" * 65, None])
def test_invalid_ticket_id_rejected(bad, mini_target, tmp_path):
    with pytest.raises(WorkspaceError):
        prepare_workspace(bad, source=mini_target, runs_dir=tmp_path / "runs")
    assert not (tmp_path / "evil").exists()


def test_missing_source_rejected(tmp_path):
    with pytest.raises(WorkspaceError):
        prepare_workspace("SR-0003", source=tmp_path / "nope", runs_dir=tmp_path / "runs")


def test_reopen_keeps_work_and_overwrite_resets(ws, mini_target, work_calc):
    replace_in(work_calc, "a + b", "a - b")
    reopened = prepare_workspace(ws.ticket_id, source=mini_target, runs_dir=ws.root.parent)
    assert "a - b" in (reopened.work / "src/order_settlement/calc.py").read_text(encoding="utf-8")

    reset = prepare_workspace(ws.ticket_id, source=mini_target, runs_dir=ws.root.parent, overwrite=True)
    assert hash_tree(reset.work) == hash_tree(mini_target)


def test_open_workspace(ws, tmp_path):
    assert open_workspace(ws.ticket_id, runs_dir=ws.root.parent).root == ws.root
    with pytest.raises(WorkspaceError):
        open_workspace("SR-9999", runs_dir=ws.root.parent)


def test_compare_hashes_detects_changes(ws, work_calc):
    before = hash_tree(ws.work)
    replace_in(work_calc, "a + b", "a - b")
    write(ws.work / "new.py", "x = 1\n")
    (ws.work / "tests" / "test_calc.py").unlink()

    diff = compare_hashes(before, hash_tree(ws.work))
    assert diff == {
        "unchanged": False,
        "added": ["new.py"],
        "removed": ["tests/test_calc.py"],
        "modified": ["src/order_settlement/calc.py"],
    }
    assert compare_hashes(before, before)["unchanged"] is True


def test_verify_original_intact(ws, mini_target):
    assert verify_original_intact(ws, source=mini_target)["unchanged"] is True
    replace_in(ws.original / "src/order_settlement/calc.py", "a + b", "a * b")
    result = verify_original_intact(ws, source=mini_target)
    assert result["unchanged"] is False
    assert result["modified"] == ["src/order_settlement/calc.py"]


def test_resolve_accepts_inside(ws):
    path = resolve_in_workspace(ws, "src/order_settlement/calc.py")
    assert path == (ws.work / "src/order_settlement/calc.py").resolve()
    assert resolve_in_workspace(ws, "tests", version="original") == (ws.original / "tests").resolve()


@pytest.mark.parametrize("path", [
    "../original/src/order_settlement/calc.py",
    "../../escape.py",
    "src/../../repro_tests/test_x.py",
    "..\\original\\tests",
])
def test_resolve_rejects_outside(ws, path):
    with pytest.raises(WorkspaceError):
        resolve_in_workspace(ws, path)


def test_resolve_rejects_absolute_outside(ws, tmp_path):
    with pytest.raises(WorkspaceError):
        resolve_in_workspace(ws, tmp_path / "elsewhere.py")


def test_unknown_version_rejected(ws):
    with pytest.raises(WorkspaceError):
        ws.version_dir("main")
