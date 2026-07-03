import sys
sys.path.insert(0, r'C:\Users\navadeep\Documents\Kryth\kryth\src')
from agent.session import Session
session = Session()
print('Session profile:', getattr(session, 'profile', 'NOT SET'))