"""Fix indentation in agent_loop.py by rewriting the corrupted block."""
import os

path = os.path.join(".", "kryth", "src", "agent", "agent_loop.py")

with open(path, "rb") as f:
    raw = f.read()

sep = b"\r\n" if b"\r\n" in raw else b"\n"
lines = raw.split(sep)

# Show lines 528-575 for inspection
print("=== BEFORE (lines 528-575) ===")
for i in range(528, 575):
    s = lines[i]
    decoded = s.decode("utf-8")
    indent = len(s) - len(s.lstrip())
    safe = decoded.replace("\u2192", "->")
    print(f"  {i+1} (I{indent}): {repr(safe[:80])}")

# Rewrite lines 528-574 (1-indexed) with correct indent = 8 spaces
# We rebuild this block verbatim
TAB = b" " * 8
TAB2 = b" " * 12
TAB3 = b" " * 16

new_block = [
    b"",
    TAB + b"# Compress large tool results before storing in context",
    TAB + b"# (prevents HTML pages and search results from bloating the token count)",
    TAB + b"try:",
    TAB2 + b"if compress_result is not None:",
    TAB3 + b"result_str = compress_result(tool_name, str(result))",
    TAB2 + b"else:",
    TAB3 + b"result_str = str(result)",
    TAB + b"except Exception:",
    TAB2 + b"result_str = str(result)",
    b"",
    TAB + b"# Preserve full file content for the read panel renderer, but collapse to",
    TAB + b"# a one-line summary for context messages and read memory (token economy).",
    TAB + b"if tool_name in (\"read\", \"read_file\"):",
    TAB2 + b"try:",
    TAB3 + b"_rpath = args.get(\"path\", \"\")",
    TAB3 + b"_parts = []",
    TAB3 + b"if args.get(\"limit\") is not None:",
    TAB3.replace(b" ", b"", 1) + b"_parts.append(f\"limit={args.get(chr(39)+chr(95)+chr(108)+chr(105)+chr(109)+chr(105)+chr(116))}\")",
    b"# placeholder comment",
    b"",
]

# Just replace lines 528-574 with properly indented originals
# Read original from git to get clean version
import subprocess
result = subprocess.run(
    ["git", "show", "HEAD:kryth/src/agent/agent_loop.py"],
    capture_output=True
)
original = result.stdout
if b"\r\n" in original:
    orig_lines = original.split(b"\r\n")
else:
    orig_lines = original.split(b"\n")

# The original lines 529-570 (0-indexed: 528-569)
orig_block = orig_lines[528:570]
print("\n=== ORIGINAL BLOCK (from git) ===")
for i, l in enumerate(orig_block):
    decoded = l.decode("utf-8")
    safe = decoded.replace("\u2192", "->")
    print(f"  {528+i+1}: {repr(safe[:80])}")

# Reconstruct: replace each line with indent=8 preserved
fixed_block = []
for l in orig_block:
    decoded = l.decode("utf-8")
    stripped = decoded.lstrip()
    indent_level = len(decoded) - len(stripped)
    # Normalize to 8-space indent for the fix block
    new_indent = 8 + indent_level
    fixed_block.append((" " * new_indent + stripped).encode("utf-8") if stripped else b"")

# Now splice into existing file
lines[528:570] = fixed_block

print("\n=== NEW BLOCK ===")
for i, l in enumerate(fixed_block):
    decoded = l.decode("utf-8")
    print(f"  {528+i+1}: {repr(decoded[:80])}")

with open(path, "wb") as f:
    f.write(sep.join(lines))

print("\n--- linecount:", len([l for l in lines if l.strip()]))
