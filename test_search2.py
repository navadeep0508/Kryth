import sys
sys.path.insert(0, 'kryth/src')
from agent.handlers.search_handler import run_search
import time

start = time.perf_counter()
result = run_search('find routes', 'kryth/src/agent')
latency = time.perf_counter() - start
print(f'Latency: {latency:.1f}s')
print(f'Status: {result["status"]}')
print(f'Matches: {result.get("matches", 0)}')
print(f'Files read: {result.get("files_read", 0)}')