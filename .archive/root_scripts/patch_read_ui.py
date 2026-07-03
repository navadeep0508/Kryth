"""Fix: preserve full read_file content for the read-panel UI renderer.

The agent loop was stripping read_file results down to a one-line summary
BEFORE the UI renderer saw them — so the panel in tool_results.py never got
the actual file content.  This patch:
 1. Saves the full result as _ui_result before stripping it
 2. Passes _ui_result to ui.tool_result() for reads only
 3. Keeps result_str token-cheap for context messages and append_tool_msg
"""
import re

path = "kryth/src/agent/agent_loop.py"
with open(path, encoding="utf-8") as f:
    src = f.read()

# --- Replace the block that strips read results ---
old_block = '''    # Never show raw file content in the CLI; replace read results with a one-line summary.
    if tool_name in ("read", "read_file"):
        try:
            _rpath = args.get("path", "")
            _parts = []
            if args.get("limit") is not None:
                _parts.append(f"limit={args.get('limit')}")
            if args.get("offset") is not None:
                _parts.append(f"offset={args.get('offset')}")
            if args.get("ranges"):
                _parts.append(f"ranges={args.get('ranges')}")
            _param_str = f"[{', '.join(_parts)}]" if _parts else ""
            result_str = f"->Read {_rpath} {_param_str}".strip()
            session.read_memory[_rpath] = result_str
        except Exception:
            pass'''

new_block = '''    # Preserve full content for read-file panel renderer; token-cheap summary
    # is stored in read_memory and result_str for context messages.
    if tool_name in ("read", "read_file"):
        _ui_result = str(result)
        try:
            _rpath = args.get("path", "")
            _parts = []
            if args.get("limit") is not None:
                _parts.append(f"limit={args.get('limit')}")
            if args.get("offset") is not None:
                _parts.append(f"offset={args.get('offset')}")
            if args.get("ranges"):
                _parts.append(f"ranges={args.get('ranges')}")
            _param_str = f"[{', '.join(_parts)}]" if _parts else ""
            result_str = f"->Read {_rpath} {_param_str}".strip()
            session.read_memory[_rpath] = result_str
        except Exception:
            pass
    else:
        _ui_result = result_str'''

if old_block not in src:
    print("ERROR: old block not found — file may already be modified")
else:
    src = src.replace(old_block, new_block)

# --- Replace the ui.tool_result emission to pass _ui_result for reads ---
old_emit = '''    # Tools that render their own visual representation skip the generic tee
    if tool_name not in SELF_RENDERED_TOOLS:
        ui.tool_result(result_str, error=has_error(result_str))
        _append_tool_msg(session, call_id, tool_name, result_str)'''

new_emit = '''    # Tools that render their own visual representation skip the generic tee.
    # For reads we pass the full _ui_result so the panel renderer can truncate
    # and syntax-highlight; for everything else result_str is the token-cheap form.
    if tool_name not in SELF_RENDERED_TOOLS:
        _emit_result = _ui_result if tool_name in ("read", "read_file") else result_str
        ui.tool_result(_emit_result, error=has_error(result_str))
        _append_tool_msg(session, call_id, tool_name, result_str)'''

if old_emit not in src:
    print("ERROR: old emit block not found")
else:
    src = src.replace(old_emit, new_emit)

with open(path, "w", encoding="utf-8") as f:
    f.write(src)

print("Done - agent_loop.py patched")
