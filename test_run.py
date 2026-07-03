import sys
sys.path.insert(0, 'kryth/src')
from agent.handlers.run_handler import run_and_verify, detect_stack
import time

print('=== detect_stack ===')
start = time.perf_counter()
stack = detect_stack('.')
latency = time.perf_counter() - start
print(f'Latency: {latency:.1f}s')
print(f'Stack: {stack}')

print('\n=== run_and_verify (quick test) ===')
start = time.perf_counter()
result = run_and_verify('.', timeout=10)
latency = time.perf_counter() - start
print(f'Latency: {latency:.1f}s')
print(f'Status: {result["status"]}')
print(f'Exit code: {result["exit_code"]}')
print(f'Server running: {result["server_running"]}')
print(f'Port: {result["port"]}')