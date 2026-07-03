#!/usr/bin/env python3
"""Properly fix encoding by decoding as latin-1 and re-encoding as UTF-8"""

with open('src/agent/agent_loop.py', 'rb') as f:
    content_bytes = f.read()

# Decode as latin-1 (maps all bytes 0-255 to Unicode code points 0-255)
# This will interpret any byte as a valid character
text = content_bytes.decode('latin-1')

# Now re-encode to UTF-8
utf8_bytes = text.encode('utf-8')

# Write back
with open('src/agent/agent_loop.py', 'wb') as f:
    f.write(utf8_bytes)

print("File re-encoded from latin-1 to UTF-8")
print(f"Original size: {len(content_bytes)} bytes")
print(f"New size: {len(utf8_bytes)} bytes")

# Verify it's valid UTF-8
try:
    utf8_bytes.decode('utf-8')
    print("File is now valid UTF-8")
except UnicodeDecodeError as e:
    print(f"Still invalid UTF-8: {e}")