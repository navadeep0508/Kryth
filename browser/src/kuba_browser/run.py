import json, os, sys, time, urllib.request
from io import StringIO

# Windows default stdout/stderr encoding is cp1252
# which can't encode the 🐴 marker helpers prepend to tab titles (or anything
# else outside the locale charset). Force UTF-8 so `print(page_info())` and
# tracebacks carrying page titles don't UnicodeEncodeError on Windows. #124(4).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try: _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

from .admin import (
    _version,
    NAME,
    daemon_alive,
    daemon_browser_kind,
    ensure_daemon,
    list_local_profiles,
    print_update_banner,
    require_existing_daemon,
    restart_daemon,
    run_doctor,
    run_doctor_fix_snap,
    run_doctor_json,
    run_update,
)
from . import recorder
from .helpers import *

HELP = """KUBA Browser Agent

Read SKILL.md for the default workflow and examples.

Typical usage:
  kuba-browser <<'PY'
  ensure_real_tab()
  print(page_info())
  PY

Helpers are pre-imported. The daemon auto-starts and connects to the running browser.

Commands:
  kuba-browser --version        print the installed version
  kuba-browser --doctor         diagnose install, daemon, and browser state
  kuba-browser doctor           same as --doctor
  kuba-browser doctor --json [--require-existing-daemon]
                                    print machine-readable runtime health
  kuba-browser doctor --fix-snap   print how to fix Snap Chromium blocking CDP (Linux)
  kuba-browser mac-approve         approve Chrome's macOS remote debugging sheet
  kuba-browser skill               print the kuba-browser skill text
  kuba-browser recordings          show recording status and recent sessions
  kuba-browser recordings --latest   print the newest recording directory
  kuba-browser recordings enable   save browser actions locally by default
  kuba-browser recordings disable  stop saving browser actions by default
  kuba-browser video init <recording>      prepare a recording for editing
  kuba-browser video review <recording>    compile and review the video
  kuba-browser video export <recording> --reviewed   export a verified MP4
  kuba-browser --update [-y]    pull the latest version (agents: pass -y)
  kuba-browser --reload         stop the daemon so next call picks up code changes
"""

USAGE = """Usage:
  kuba-browser <<'PY'
  print(page_info())
  PY
"""


# Probe /json/version (not a bare TCP connect) so a non-Chrome process bound to
# 9222/9223 doesn't masquerade as Chrome. Mirrors daemon.py's fallback probe.
def _local_chrome_listening():
    for port in (9222, 9223):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.3) as response:
                version = json.loads(response.read())
            if isinstance(version, dict) and isinstance(version.get("webSocketDebuggerUrl"), str) and version["webSocketDebuggerUrl"]:
                return True
        except (OSError, TypeError, ValueError):
            pass
    return False


# BU_CDP_URL / BU_CDP_WS override local Chrome discovery (install.md:58-59),
# so they must be respected before the daemon connects.
def _explicit_cdp_configured():
    return bool(os.environ.get("BU_CDP_URL") or os.environ.get("BU_CDP_WS"))


def _print_skill():
    from importlib import resources
    # SKILL.md is UTF-8 (contains emoji); locale-codec read crashes on gbk Windows
    print(resources.files("kuba_browser").joinpath("SKILL.md").read_text(encoding="utf-8"), end="")


def _exit_code(result) -> int:
    if result is None:
        return 0
    if isinstance(result, int):
        return result
    return 1

_MAX_TRACED_STEPS = 500
_MAX_STEP_ARGS_LENGTH = 300
_helper_trace = []
_helper_call_count = 0


def _step_args(args, kwargs):
    parts = [repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()]
    return ", ".join(parts)[:_MAX_STEP_ARGS_LENGTH]


def _traced(name, fn):
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        global _helper_call_count
        _helper_call_count += 1
        entry = {"helper": name, "args": _step_args(args, kwargs)}
        if len(_helper_trace) < _MAX_TRACED_STEPS:
            _helper_trace.append(entry)
        step_start = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:
            entry["duration_seconds"] = round(time.monotonic() - step_start, 3)
            entry["error"] = str(exc)[:300]
            raise
        entry["duration_seconds"] = round(time.monotonic() - step_start, 3)
        recorder.observe(name, args, kwargs, entry["duration_seconds"])
        return result

    wrapper.__KUBA_traced__ = True
    return wrapper


def _install_helper_trace():
    from . import helpers

    g = globals()
    for name in dir(helpers):
        if name.startswith("_"):
            continue
        fn = g.get(name)
        if callable(fn) and not isinstance(fn, type) and not getattr(fn, "__KUBA_traced__", False):
            g[name] = _traced(name, fn)


_MAX_OUTPUT_LENGTH = 20_000


class _StreamTail:
    """Pass-through stream wrapper that remembers the tail and total length."""

    def __init__(self, wrapped, limit=500):
        self._wrapped = wrapped
        self._limit = limit
        self.tail = ""
        self.length = 0

    def write(self, text):
        text = str(text)
        self.length += len(text)
        self.tail = (self.tail + text)[-self._limit :]
        return self._wrapped.write(text)

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


def _read_task(args):
    if args and args[0] == "--debug-clicks":
        args = args[1:]
    if args or sys.stdin.isatty():
        return None
    code = sys.stdin.read()
    sys.stdin = StringIO(code)
    return code


def _traced_steps():
    return _helper_trace or None


def main():
    global _helper_call_count
    args = sys.argv[1:]
    _helper_trace.clear()
    _helper_call_count = 0
    start_time = time.monotonic()
    task = _read_task(args)
    stderr_tail = _StreamTail(sys.stderr)
    stdout_tail = _StreamTail(sys.stdout, limit=_MAX_OUTPUT_LENGTH)
    sys.stderr = stderr_tail
    sys.stdout = stdout_tail
    try:
        _run(args)
    except SystemExit:
        raise
    except Exception:
        raise
    finally:
        sys.stderr = stderr_tail._wrapped
        sys.stdout = stdout_tail._wrapped


def _run(args):
    if args and args[0] in {"-h", "--help"}:
        print(HELP)
        return
    if args and args[0] == "--version":
        print(_version() or "unknown")
        return
    if args and args[0] == "--doctor":
        sys.exit(run_doctor())
    if args and args[0] == "doctor":
        rest = args[1:]
        if rest == ["--fix-snap"]:
            sys.exit(run_doctor_fix_snap())
        if rest and set(rest).issubset({"--json", "--require-existing-daemon"}) \
                and "--json" in rest and len(rest) == len(set(rest)):
            sys.exit(run_doctor_json(require_existing_daemon="--require-existing-daemon" in rest))
        if rest:
            print("usage: kuba-browser doctor [--fix-snap|--json [--require-existing-daemon]]", file=sys.stderr)
            sys.exit(2)
        sys.exit(run_doctor())
    if args and args[0] == "mac-approve":
        from . import macos

        sys.exit(macos.run_cli(args[1:]))
    if args and args[0] == "skill":
        if len(args) != 1:
            print("usage: kuba-browser skill", file=sys.stderr)
            sys.exit(2)
        _print_skill()
        return
    if args and args[0] == "recordings":
        rest = args[1:]
        if rest == ["--latest"]:
            latest = recorder.latest_recording()
            if latest is None:
                print("no recordings found", file=sys.stderr)
                sys.exit(1)
            print(latest)
            return
        if rest in (["enable"], ["disable"]):
            enabled = rest == ["enable"]
            recorder.set_auto_recording(enabled)
            print(f"auto-recording preference {'enabled' if enabled else 'disabled'}")
            return
        if rest:
            print("usage: kuba-browser recordings [--latest|enable|disable]", file=sys.stderr)
            sys.exit(2)
        enabled, source = recorder.auto_recording_setting()
        print(f"auto-recording: {'on' if enabled else 'off'} ({source})")
        active = recorder.recording_dir()
        print(f"active: {active or 'none'}")
        recent = recorder.recordings()
        print(f"latest: {recent[0] if recent else 'none'}")
        return
    if args and args[0] == "video":
        from . import video

        sys.exit(video.run_cli(args[1:]))
    if args and args[0] == "--update":
        rest = args[1:]
        if not set(rest).issubset({"-y", "--yes"}):
            print("usage: kuba-browser --update [-y|--yes]", file=sys.stderr)
            sys.exit(2)
        sys.exit(run_update(yes=bool(rest)))
    if args and args[0] == "--reload":
        restart_daemon()
        print("daemon stopped — will restart fresh on next call")
        return
    if args and args[0] == "--debug-clicks":
        os.environ["KUBA_DEBUG_CLICKS"] = "1"
        args = args[1:]
    if not args and not sys.stdin.isatty():
        code = sys.stdin.read()
        if not code.strip():
            sys.exit(USAGE)
    else:
        sys.exit(USAGE)
    print_update_banner()
    require_existing = os.environ.get("KUBA_REQUIRE_EXISTING_DAEMON") == "1"
    try:
        if require_existing:
            require_existing_daemon()
        else:
            ensure_daemon()
    except RuntimeError as e:
        # Setup/permission errors are instructions for calling agent
        print(f"kuba-browser: {e}", file=sys.stderr)
        sys.exit(1)
    _install_helper_trace()
    exec(code, globals())


if __name__ == "__main__":
    main()
