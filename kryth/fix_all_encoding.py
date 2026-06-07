#!/usr/bin/env python3
"""Fix all non-UTF-8 bytes in agent_loop.py"""

with open('src/agent/agent_loop.py', 'rb') as f:
    content = f.read()

# Try to decode as UTF-8 to find problematic bytes
try:
    content.decode('utf-8')
    print("File is valid UTF-8")
except UnicodeDecodeError as e:
    print(f"UTF-8 decode error: {e}")
    # Replace non-UTF-8 bytes with safe replacements
    # We'll decode with 'replace' to see what's wrong
    decoded = content.decode('utf-8', errors='replace')
    # Find the problematic bytes by looking for the replacement character
    # Actually, let's just replace any byte sequence that's not valid UTF-8
    # We'll use a more aggressive approach: keep only valid UTF-8 sequences
    import re
    # This regex matches valid UTF-8 byte sequences
    # Simplified: we'll just replace common problematic bytes
    # For now, let's identify all non-UTF-8 bytes
    problematic_positions = []
    for i, byte in enumerate(content):
        # Check if this is a valid start byte or continuation byte
        if byte < 0x80:
            continue  # ASCII is always valid
        elif 0xC2 <= byte <= 0xDF:
            # 2-byte sequence, next byte should be 0x80-0xBF
            if i+1 >= len(content) or not (0x80 <= content[i+1] <= 0xBF):
                problematic_positions.append(i)
        elif 0xE0 <= byte <= 0xEF:
            # 3-byte sequence
            if i+2 >= len(content):
                problematic_positions.append(i)
            else:
                b1, b2 = content[i+1], content[i+2]
                if not (0x80 <= b1 <= 0xBF) or not (0x80 <= b2 <= 0xBF):
                    problematic_positions.append(i)
        elif 0xF0 <= byte <= 0xF4:
            # 4-byte sequence
            if i+3 >= len(content):
                problematic_positions.append(i)
            else:
                b1, b2, b3 = content[i+1], content[i+2], content[i+3]
                if not (0x80 <= b1 <= 0xBF) or not (0x80 <= b2 <= 0xBF) or not (0x80 <= b3 <= 0xBF):
                    problematic_positions.append(i)
        else:
            # Invalid start byte (0x80-0xC1, 0xF5-0xFF)
            problematic_positions.append(i)

    print(f"Found {len(problematic_positions)} problematic byte positions")
    if problematic_positions:
        print("First few positions:", problematic_positions[:5])
        for pos in problematic_positions[:5]:
            start = max(0, pos-10)
            end = min(len(content), pos+10)
            print(f"  At {pos}: {repr(content[start:end])}")

    # Replace problematic bytes with a safe character (e.g., '-')
    for pos in problematic_positions:
        content = content[:pos] + b'-' + content[pos+1:]

    # Write back
    with open('src/agent/agent_loop.py', 'wb') as f:
        f.write(content)

    print(f"Fixed {len(problematic_positions)} bytes")
else:
    print("No encoding issues found")