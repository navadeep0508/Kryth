import sys
sys.path.insert(0, 'kryth/src')
from agent.handlers.read_handler import summarize_project
import time

start = time.perf_counter()
result = summarize_project('kryth/src/agent')
latency = time.perf_counter() - start
print(f'Latency: {latency:.1f}s')
print(f'Result: {result[:100]}...')