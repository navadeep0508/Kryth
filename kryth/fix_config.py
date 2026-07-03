path = r'C:\Users\navadeep\Documents\Kryth\kryth\src\kryth\config.py'
with open(path, 'rb') as f:
    data = f.read()

# Find the corrupted bytes sequence
corrupt = b'\xc3\xa2\xe2\x80\xa0'
right = '→'
down = '↓'

# Replace both occurrences: corrupted+quote → correct arrow+quotes
data = data.replace(b'\xc3\xa2\xe2\x80\xa0\' / \xc3\xa2\xe2\x80\xa0\"',
                    b'\xe2\x86\x92\' / \xe2\x86\x93\"')

with open(path, 'wb') as f:
    f.write(data)

# Verify
with open(path, 'rb') as f:
    data = f.read()
lines = data.split(b'\n')
for i, line in enumerate(lines[159:167], start=160):
    print(f'{i}: {line.decode("utf-8")}')
