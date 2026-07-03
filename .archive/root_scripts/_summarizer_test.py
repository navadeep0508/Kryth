import sys
sys.path.insert(0, r'C:\Users\navadeep\Documents\Kryth\kryth\src')

from agent.memory.post_read_summarizer import summarize_file

# Read test file
test_path = r'C:\Users\navadeep\Documents\Kryth\test_hello.py'
with open(test_path, 'r') as f:
    content = f.read()

summary = summarize_file(test_path, content)
print('Summary:')
print(summary.to_context_block())
print()
print('Functions:', summary.functions)
print('Classes:', summary.classes)
print('Purpose length:', len(summary.purpose))