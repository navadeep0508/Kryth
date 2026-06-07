"""Pytest configuration for KRYTH tests.

Sets up the Python path so the 'agent' package can be imported from src/
when running tests from the project root. Also provides fixtures for
session isolation.
"""

import sys
from pathlib import Path

# Add the src directory to Python path
project_root = Path(__file__).parent.parent
src_dir = project_root / "src"
if src_dir.exists() and str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# Also add the project root itself (for kryth/ imports)
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


import pytest
from agent.session import _current, pop_session

# Exclude test files whose imports depend on unimplemented stubs so they
# don't block collection of the rest of the suite.
collect_ignore = [
    "test_retrieval_advanced.py",
]

@pytest.fixture(autouse=True)
def clean_session_context():
    """Reset the session context before each test to ensure isolation."""
    # Clear any existing session
    _current.set(None)
    yield
    # Cleanup after test
    _current.set(None)