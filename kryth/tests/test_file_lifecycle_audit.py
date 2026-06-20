"""Phase 2/4 audit — file-operation lifecycle through the REAL tool functions.

Validation Supervisor coverage: exercises write/read/edit/delete (and the
rename = write-new + delete-old pattern) against the actual tool callables in
the registry, asserting both the on-disk effect AND the post-execution
validation message. Deterministic (no model), so it gates CI regardless of
provider speed — complementing the live harness (tests/live_validation.py).
"""

import sys
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(autouse=True)
def _silence_ui(monkeypatch):
    import agent.ui as ui
    for n in dir(ui):
        f = getattr(ui, n, None)
        if callable(f) and not n.startswith("_"):
            try:
                monkeypatch.setattr(ui, n, lambda *a, **k: None)
            except Exception:
                pass
    yield


def _tools():
    from agent.tools import TOOLS
    return TOOLS


# ── Create ───────────────────────────────────────────────────────────────────

def test_write_creates_file_and_validates(tmp_path):
    wf = _tools()["write_file"]
    p = tmp_path / "hello.txt"
    res = wf(str(p), "hello kryth")
    assert p.exists()
    assert p.read_text() == "hello kryth"
    # Post-execution validation message (Phase 2/3 contract).
    assert res.startswith("✓"), f"expected validated success, got: {res!r}"
    assert "bytes on disk" in res


# ── Read ─────────────────────────────────────────────────────────────────────

def test_read_returns_content(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("line one\nline two")
    rf = _tools()["read_file"]
    out = rf(str(p))
    assert "line one" in out and "line two" in out


def test_read_missing_file_errors_cleanly(tmp_path):
    rf = _tools()["read_file"]
    out = rf(str(tmp_path / "nope.txt"))
    assert "[ERROR" in out or "not found" in out.lower()


# ── Edit (minimal, targeted) ─────────────────────────────────────────────────

def test_edit_makes_minimal_change(tmp_path):
    p = tmp_path / "main.py"
    p.write_text("x = 1\ny = 2\nz = 3\n")
    ef = _tools()["edit_file"]
    res = ef(str(p), "y = 2", "y = 20")
    txt = p.read_text()
    assert "y = 20" in txt
    # Unrelated lines untouched (Phase 4: no unrelated changes).
    assert "x = 1" in txt and "z = 3" in txt


def test_edit_ambiguous_is_rejected(tmp_path):
    p = tmp_path / "dup.py"
    p.write_text("a = 1\na = 1\n")
    ef = _tools()["edit_file"]
    res = ef(str(p), "a = 1", "a = 2")
    # Ambiguous match must error, not silently corrupt the file.
    assert "[ERROR" in res or "AMBIGUOUS" in res.upper()
    assert p.read_text() == "a = 1\na = 1\n"  # unchanged


# ── Delete ───────────────────────────────────────────────────────────────────

def test_delete_removes_and_validates(tmp_path):
    p = tmp_path / "gone.txt"
    p.write_text("bye")
    df = _tools()["delete_file"]
    res = df(str(p))
    assert not p.exists()
    assert res.startswith("✓")


def test_delete_missing_errors(tmp_path):
    df = _tools()["delete_file"]
    res = df(str(tmp_path / "absent.txt"))
    assert "[ERROR" in res or "not found" in res.lower()


# ── Rename (create-new + delete-old) ─────────────────────────────────────────

def test_rename_pattern_old_gone_new_exists(tmp_path):
    wf, df = _tools()["write_file"], _tools()["delete_file"]
    old = tmp_path / "hello.txt"
    new = tmp_path / "notes.txt"
    wf(str(old), "content")
    # rename = write new with same content, delete old
    wf(str(new), old.read_text())
    df(str(old))
    assert new.exists() and not old.exists()
    assert new.read_text() == "content"


# ── Full lifecycle round-trip (Phase 2 end-to-end) ───────────────────────────

def test_full_lifecycle(tmp_path):
    wf, rf, df = _tools()["write_file"], _tools()["read_file"], _tools()["delete_file"]
    p = tmp_path / "lifecycle.txt"
    assert wf(str(p), "helo sunny").startswith("✓")
    assert "helo sunny" in rf(str(p))
    assert df(str(p)).startswith("✓")
    assert not p.exists()


# ── JSON write integrity (Phase 3 multi-file content correctness) ────────────

def test_json_file_written_valid(tmp_path):
    wf = _tools()["write_file"]
    p = tmp_path / "config.json"
    payload = {"name": "kryth", "version": 2, "nested": {"a": [1, 2, 3]}}
    wf(str(p), json.dumps(payload, indent=2))
    assert json.loads(p.read_text()) == payload
