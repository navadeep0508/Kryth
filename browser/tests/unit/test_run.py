import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from kuba_browser import run


def test_stdin_executes_code():
    stdout = StringIO()
    fake_stdin = StringIO("print('hello from stdin')")

    with patch.object(sys, "argv", ["kuba-browser"]), \
         patch("kuba_browser.run.ensure_daemon"), \
         patch("kuba_browser.run.print_update_banner"), \
         patch("sys.stdin", fake_stdin), \
         patch("sys.stdout", stdout):
        run.main()

    assert stdout.getvalue().strip() == "hello from stdin"


def test_require_existing_daemon_never_auto_starts(monkeypatch):
    monkeypatch.setenv("KUBA_REQUIRE_EXISTING_DAEMON", "1")
    with patch.object(sys, "argv", ["kuba-browser"]), \
         patch("sys.stdin", StringIO("x = 1")), \
         patch("kuba_browser.run.require_existing_daemon") as mock_require, \
         patch("kuba_browser.run.ensure_daemon") as mock_ensure, \
         patch("kuba_browser.run.print_update_banner"):
        run.main()

    mock_require.assert_called_once_with()
    mock_ensure.assert_not_called()


def test_c_flag_is_rejected():
    with patch.object(sys, "argv", ["kuba-browser", "-c", "print('old path')"]), \
         patch("sys.stdin", StringIO("print('ignored')")):
        try:
            run.main()
        except SystemExit as e:
            assert "kuba-browser <<'PY'" in str(e)
        else:
            raise AssertionError("-c should be rejected")


def test_no_args_interactive_stdin_prints_usage():
    fake_stdin = StringIO("")
    fake_stdin.isatty = lambda: True

    with patch.object(sys, "argv", ["kuba-browser"]), \
         patch("sys.stdin", fake_stdin):
        try:
            run.main()
        except SystemExit as e:
            assert "kuba-browser <<'PY'" in str(e)
        else:
            raise AssertionError("interactive no-args invocation should exit with usage")


def test_no_args_empty_stdin_prints_usage():
    with patch.object(sys, "argv", ["kuba-browser"]), \
         patch("sys.stdin", StringIO("")):
        try:
            run.main()
        except SystemExit as e:
            assert "kuba-browser <<'PY'" in str(e)
        else:
            raise AssertionError("empty stdin should exit with usage")


def test_explicit_cdp_configured_helper_truthy(monkeypatch):
    """Direct unit test of the helper: any non-empty BU_CDP_URL or BU_CDP_WS must
    return True so the bootstrap guard reads as 'caller has been explicit'."""
    for name, value in [
        ("BU_CDP_URL", "http://127.0.0.1:9333"),
        ("BU_CDP_WS", "ws://example.test/devtools/browser/abc"),
        ("BU_CDP_URL", "http://[::1]:9333"),  # IPv6 host
        ("BU_CDP_WS", "wss://cloud.example.com/devtools/browser/x"),  # secure WS
    ]:
        monkeypatch.delenv("BU_CDP_URL", raising=False)
        monkeypatch.delenv("BU_CDP_WS", raising=False)
        monkeypatch.setenv(name, value)
        assert run._explicit_cdp_configured() is True, f"{name}={value!r} should be truthy"


def test_explicit_cdp_configured_helper_falsy(monkeypatch):
    """Helper must return False for unset, empty-string, or both-unset cases —
    those are all 'caller has not chosen an endpoint' from the bootstrap's POV."""
    monkeypatch.delenv("BU_CDP_URL", raising=False)
    monkeypatch.delenv("BU_CDP_WS", raising=False)
    assert run._explicit_cdp_configured() is False, "both unset"
    monkeypatch.setenv("BU_CDP_URL", "")
    assert run._explicit_cdp_configured() is False, "BU_CDP_URL empty string"
    monkeypatch.delenv("BU_CDP_URL", raising=False)
    monkeypatch.setenv("BU_CDP_WS", "")
    assert run._explicit_cdp_configured() is False, "BU_CDP_WS empty string"


def test_local_chrome_listening_rejects_non_chrome():
    """A bare TCP listener on 9222/9223 must not fool the probe — only a real
    /json/version response with a DevTools WebSocket counts as Chrome."""
    with patch("kuba_browser.run.urllib.request.urlopen", side_effect=OSError):
        assert run._local_chrome_listening() is False
    for payload in (b"not JSON", b"{}", b"[]", b'{"webSocketDebuggerUrl": ""}', b'{"webSocketDebuggerUrl": 1}'):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = payload
        with patch("kuba_browser.run.urllib.request.urlopen", return_value=response) as mock_open:
            assert run._local_chrome_listening() is False
        assert mock_open.call_count == 2


def test_local_chrome_listening_accepts_devtools_response():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'{"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/x"}'
    with patch("kuba_browser.run.urllib.request.urlopen", return_value=response) as mock_open:
        assert run._local_chrome_listening() is True
    mock_open.assert_called_once()


def test_cli_doctor_fix_snap_invokes_guide():
    with patch.object(sys, "argv", ["kuba-browser", "doctor", "--fix-snap"]), \
         patch("kuba_browser.run.run_doctor_fix_snap", return_value=0) as m:
        with pytest.raises(SystemExit) as ei:
            run.main()
    assert ei.value.code == 0
    m.assert_called_once()


def test_cli_doctor_rejects_unknown_flags():
    err = StringIO()
    with patch.object(sys, "argv", ["kuba-browser", "doctor", "--bogus"]), patch("sys.stderr", err):
        with pytest.raises(SystemExit) as ei:
            run.main()
    assert ei.value.code == 2
    assert "usage" in err.getvalue().lower()


def test_cli_update_rejects_unknown_flags():
    err = StringIO()
    with patch.object(sys, "argv", ["kuba-browser", "--update", "--bogus"]), \
         patch("sys.stderr", err), \
         patch("kuba_browser.run.run_update") as m:
        with pytest.raises(SystemExit) as ei:
            run.main()
    assert ei.value.code == 2
    assert "usage" in err.getvalue().lower()
    m.assert_not_called()


@pytest.mark.parametrize("flag,expected_yes", [(None, False), ("-y", True), ("--yes", True)])
def test_cli_update_forwards_yes_flag(flag, expected_yes):
    argv = ["kuba-browser", "--update"] + ([flag] if flag else [])
    with patch.object(sys, "argv", argv), \
         patch("kuba_browser.run.run_update", return_value=0) as m:
        with pytest.raises(SystemExit) as ei:
            run.main()
    assert ei.value.code == 0
    m.assert_called_once_with(yes=expected_yes)
