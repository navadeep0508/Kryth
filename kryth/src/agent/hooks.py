import json
import os
import re
import subprocess

from agent.settings import load_settings


HOOK_BLOCK_PREFIX = "[HOOK_BLOCKED]"


def run_hooks(event: str, tool_name: str = "", tool_args: dict | None = None, tool_result: str | None = None) -> str | None:
    cfg = load_settings().get("hooks", {}).get(event, [])
    if not cfg:
        return None

    outputs: list[str] = []

    for hook in cfg:
        matcher = hook.get("matcher", ".*")
        cmd = hook.get("command", "")
        if not cmd:
            continue

        if tool_name and not re.search(matcher, tool_name):
            continue

        env = dict(os.environ)
        env["KRYTH_EVENT"] = event
        if tool_name:
            env["KRYTH_TOOL"] = tool_name
        if tool_args is not None:
            env["KRYTH_TOOL_ARGS"] = json.dumps(tool_args)[:4000]
        if tool_result is not None:
            env["KRYTH_TOOL_RESULT"] = str(tool_result)[:4000]

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            outputs.append(f"hook timeout: {cmd}")
            continue
        except Exception as e:
            outputs.append(f"hook error: {e}")
            continue

        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()

        if event == "PreToolUse" and result.returncode != 0:
            reason = out or err or f"hook exit={result.returncode}"
            return f"{HOOK_BLOCK_PREFIX} {reason}"

        merged = out or err
        if merged:
            outputs.append(merged)

    if not outputs:
        return None
    return "\n".join(outputs)
