#!/usr/bin/env python3
"""Fix non-UTF-8 byte in agent_loop.py"""

with open('src/agent/agent_loop.py', 'rb') as f:
    content = f.read()

# Find the problematic byte
idx = content.find(b'\x97')
if idx != -1:
    print(f"Found problematic byte 0x97 at position {idx}")
    # Show context
    start = max(0, idx - 20)
    end = min(len(content), idx + 20)
    print("Context (hex):", content[start:end].hex())
    print("Context (repr):", repr(content[start:end]))
    # Replace with a proper UTF-8 em dash or just a regular dash
    # The original text was: "— fall through to the pipeline/planner path below."
    # That's an em dash (U+2014) which in UTF-8 is e2 80 94
    # But we can just use a regular hyphen to be safe
    content = content.replace(b'\x97', b'-')
    with open('src/agent/agent_loop.py', 'wb') as f:
        f.write(content)
    print("Fixed: replaced 0x97 with hyphen")
else:
    print("No problematic byte found")