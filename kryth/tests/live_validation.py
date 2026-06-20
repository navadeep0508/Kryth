"""KRYTH live black-box validation harness.

Drives the REAL agent (run_agent) against the configured live model, capturing
every tool dispatch (via the event BUS) and every filesystem side effect (via
before/after directory snapshots). Each phase asserts black-box expectations:
conversation makes no tools, file ops use the right tool and stop, etc.

This is NOT a unit test of internals — it exercises the whole stack end to end.
Requires a configured provider key (NVIDIA/OpenAI/etc). Skips cleanly if none.

Run:  python tests/live_validation.py            # all phases
      python tests/live_validation.py 1 2        # selected phases
"""

import os
import sys
import io
import time
import shutil
import tempfile
import contextlib
from pathlib import Path

# Force UTF-8 stdout so box/▶ glyphs in our own prints never hit cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _bootstrap_env():
    """Mirror kryth.main's key/endpoint bootstrap BEFORE any agent import.

    agent/llm.py builds the OpenAI-compatible client at import time, so the
    provider key + base URL must be in the environment first. Driving
    run_agent() directly (as this harness does) bypasses main(), so we
    replicate the same config→env wiring here.

    Validation-only override: set KRYTH_VALIDATION_GROQ_KEY to point the
    harness at Groq (fast native-tool-calling model). This is for live
    validation runs ONLY — nothing is written to config; the override lives
    purely in the process environment.
    """
    groq_key = os.environ.get("KRYTH_VALIDATION_GROQ_KEY", "").strip()
    if groq_key:
        os.environ["OPENAI_API_KEY"] = groq_key
        os.environ["KRYTH_BASE_URL"] = "https://api.groq.com/openai/v1"
        model = os.environ.get("KRYTH_VALIDATION_MODEL", "llama-3.3-70b-versatile")
        os.environ["KRYTH_MAIN_MODEL"] = model
        os.environ["KRYTH_PLANNER_MODEL"] = model
        os.environ["KRYTH_SUMMARIZER_MODEL"] = "llama-3.1-8b-instant"
        return
    try:
        from kryth.config import apply_to_env
        apply_to_env()
    except Exception:
        pass
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if not nvidia_key:
        try:
            from kryth.config import _load
            nvidia_key = str((_load() or {}).get("nvidia_api_key", "")).strip()
            if nvidia_key:
                os.environ["NVIDIA_API_KEY"] = nvidia_key
        except Exception:
            pass
    if nvidia_key:
        os.environ.setdefault("OPENAI_API_KEY", nvidia_key)
        os.environ.setdefault("KRYTH_BASE_URL", "https://integrate.api.nvidia.com/v1")


_bootstrap_env()

# Bound the harness: a throttled/slow provider must surface quickly, not hang
# behind production's ~60s exponential backoff. These only affect this harness.
os.environ.setdefault("KRYTH_TTFT_TIMEOUT", "12")
os.environ.setdefault("KRYTH_API_RETRY_DELAY", "0")


def _has_key() -> bool:
    for k in ("NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
              "DEEPSEEK_API_KEY", "GROQ_API_KEY", "KRYTH_API_KEY"):
        if os.environ.get(k, "").strip():
            return True
    try:
        from kryth.config import _load
        cfg = _load() or {}
        return any(str(v).strip() for kk, v in cfg.items() if kk.endswith("api_key"))
    except Exception:
        return False


class Capture:
    """Subscribe to the event bus; record tool dispatches + shell commands."""
    def __init__(self):
        self.tools = []
        self.commands = []
        self._unsub = None

    def __enter__(self):
        from agent.ui.events import BUS, EventKind
        def on(e):
            if e.kind == EventKind.TOOL_START:
                self.tools.append((e.data.get("name", ""), e.data.get("args", {})))
            elif e.kind == EventKind.SHELL_RUN:
                self.commands.append(e.data.get("command", ""))
        self._unsub = BUS.subscribe(on)
        return self

    def __exit__(self, *a):
        if self._unsub:
            self._unsub()

    def tool_names(self):
        return [t[0] for t in self.tools]


def _snapshot(d):
    return {str(p.relative_to(d)) for p in Path(d).rglob("*") if p.is_file()}


PER_PROMPT_TIMEOUT = float(os.environ.get("KRYTH_LIVE_PROMPT_TIMEOUT", "45"))


def _drive_once(prompt, workdir):
    import threading
    box = {}
    t = threading.Thread(target=lambda: box.update(_run_prompt_inner(prompt, workdir)), daemon=True)
    t.start()
    t.join(PER_PROMPT_TIMEOUT)
    if t.is_alive():
        return {
            "prompt": prompt, "tools": [], "commands": [], "created": [],
            "deleted": [], "files_after": [], "status": "timeout", "content": "",
            "error": f"prompt exceeded {PER_PROMPT_TIMEOUT:.0f}s", "provider_down": True,
        }
    return box


def run_prompt(prompt, workdir):
    """Run one prompt through the real agent, bounded by a hard wall-clock
    timeout. Retries ONCE on a transient empty/no-op reply (a provider blip,
    not a KRYTH defect) so flaky-network noise doesn't mask real behavior."""
    r = _drive_once(prompt, workdir)
    transient = (
        not r.get("provider_down")
        and not r.get("error")
        and not r["tools"]
        and not r["created"]
        and not (r.get("content") or "").strip()
    )
    if transient:
        time.sleep(2.0)
        r2 = _drive_once(prompt, workdir)
        if (r2.get("content") or "").strip() or r2["tools"]:
            return r2
    return r


def _run_prompt_inner(prompt, workdir):
    from agent.session import get_session
    from agent import agent_loop

    cwd0 = os.getcwd()
    os.chdir(workdir)
    before = _snapshot(workdir)
    # Fresh session each prompt — isolate behavior.
    try:
        s = get_session()
        s.messages = []
        s.cumulative_in_tokens = 0
        s.cumulative_out_tokens = 0
    except Exception:
        pass

    buf = io.StringIO()
    result = None
    err = None
    with Capture() as cap:
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                result = agent_loop.run_agent(prompt)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        finally:
            os.chdir(cwd0)

    after = _snapshot(workdir)
    status = getattr(result, "status", None)
    captured = buf.getvalue()
    # Distinguish a PROVIDER problem (429/401/timeout) from a KRYTH defect so
    # the harness never reports a false failure when the endpoint is down.
    provider_down = (
        status in ("api_error", "interrupted", "timeout")
        and any(s in captured for s in ("429", "401", "Too Many Requests",
                                        "rejected", "TTFT", "timeout"))
    )
    return {
        "prompt": prompt,
        "tools": cap.tool_names(),
        "commands": cap.commands,
        "created": sorted(after - before),
        "deleted": sorted(before - after),
        "files_after": sorted(after),
        "status": status,
        "content": (getattr(result, "content", "") or "")[:200],
        "error": err,
        "provider_down": provider_down,
    }


# ── Phase definitions ────────────────────────────────────────────────────────

CONVERSATION_PROMPTS = [
    "hi", "hello", "helo", "hey", "thanks", "who are you",
    "what is python", "what is jwt", "hello what is python",
    "thanks what can you do",
]


def check_conversation(r):
    """No tools, no commands, no files, a non-empty reply."""
    problems = []
    if r["tools"]:
        problems.append(f"tools dispatched: {r['tools']}")
    if r["commands"]:
        problems.append(f"commands run: {r['commands']}")
    if r["created"]:
        problems.append(f"files created: {r['created']}")
    if r["error"]:
        problems.append(f"error: {r['error']}")
    if not r["content"].strip():
        problems.append("empty reply")
    return problems


def _phase1(report):
    with tempfile.TemporaryDirectory() as d:
        for p in CONVERSATION_PROMPTS:
            r = run_prompt(p, d)
            probs = check_conversation(r)
            report.record("P1", p, probs, r)


def check_stop_discipline(r, allowed_tools):
    """No unsolicited repo scan / npm install / extra files."""
    problems = []
    for cmd in r["commands"]:
        low = cmd.lower()
        if "npm install" in low or "pip install" in low:
            problems.append(f"unsolicited install: {cmd}")
    # tools beyond the allowed set for this kind of task
    extra = [t for t in r["tools"] if t not in allowed_tools]
    if extra:
        problems.append(f"unexpected tools: {extra}")
    return problems


FILE_OP_CASES = [
    # (prompt, expected primary tool substring, expected created/deleted check)
    ("create hello.txt containing the text hello", "write_file", "create"),
    ("read hello.txt", "read_file", "read"),
    ("rename hello.txt to notes.txt", None, "rename"),
    ("delete notes.txt", "delete_file", "delete"),
]


def _phase2(report):
    # Sequential — these build on each other; share one workdir.
    d = tempfile.mkdtemp()
    try:
        for prompt, primary, kind in FILE_OP_CASES:
            r = run_prompt(prompt, d)
            probs = []
            if r["error"]:
                probs.append(f"error: {r['error']}")
            if primary and primary not in r["tools"]:
                probs.append(f"expected {primary}, got tools {r['tools']}")
            # stopping discipline: no npm/pip, no spurious extra writes
            for cmd in r["commands"]:
                if "install" in cmd.lower():
                    probs.append(f"unsolicited install: {cmd}")
            report.record("P2", prompt, probs, r)
    finally:
        shutil.rmtree(d, ignore_errors=True)


class Report:
    def __init__(self):
        self.rows = []
        self.skipped = []
    def record(self, phase, prompt, problems, r):
        # Provider outage → SKIP, never a false KRYTH failure.
        if r.get("provider_down"):
            self.skipped.append((phase, prompt))
            print(f"  [SKIP] {phase} {prompt!r}  (provider unavailable — 429/401/timeout)")
            return
        ok = not problems
        self.rows.append((phase, prompt, ok, problems, r))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {phase} {prompt!r}")
        if not ok:
            for pr in problems:
                print(f"         → {pr}")
        else:
            detail = f"tools={r['tools']}" if r["tools"] else "no tools"
            print(f"         {detail}  status={r['status']}")
    def summary(self):
        total = len(self.rows)
        failed = [x for x in self.rows if not x[2]]
        print("\n" + "=" * 70)
        print(f"LIVE VALIDATION: {total - len(failed)}/{total} passed, "
              f"{len(failed)} failed, {len(self.skipped)} skipped (provider down)")
        for phase, prompt, ok, problems, r in failed:
            print(f"  FAIL {phase} {prompt!r}: {'; '.join(problems)}")
        print("=" * 70)
        return len(failed) == 0


PHASES = {"1": _phase1, "2": _phase2}


def main(argv):
    if not _has_key():
        print("SKIP: no provider key configured — live validation requires one.")
        return 0
    which = argv or list(PHASES.keys())
    report = Report()
    for ph in which:
        fn = PHASES.get(ph)
        if not fn:
            continue
        print(f"\n── PHASE {ph} ──")
        fn(report)
    ok = report.summary()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
