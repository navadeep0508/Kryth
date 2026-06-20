"""Mission definitions for the benchmark suite.

Each mission has:
    - id, name, prompt
    - setup(workspace_dir) — seeds the workspace
    - check(workspace_dir) → bool — success criterion

Missions are designed to be reproducible and fast enough to run in CI.
"""

from __future__ import annotations

import os
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class MissionDef:
    id: str
    name: str
    prompt: str
    setup: Callable[[str], None]
    check: Callable[[str], bool]
    expected_min_files: int = 1
    category: str = "coding"
    inject_failures: bool = False


# ── Workspace helpers ─────────────────────────────────────────────────────────

def _w(workspace: str, relpath: str) -> str:
    return os.path.join(workspace, relpath)


def _write(workspace: str, relpath: str, content: str) -> None:
    p = Path(workspace) / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")


def _exists(workspace: str, *relpaths: str) -> bool:
    return all((Path(workspace) / p).exists() for p in relpaths)


def _file_contains(workspace: str, relpath: str, *keywords: str) -> bool:
    p = Path(workspace) / relpath
    if not p.exists():
        return False
    text = p.read_text(encoding="utf-8", errors="ignore").lower()
    return all(kw.lower() in text for kw in keywords)


# ── Mission 1: Fix one small bug ──────────────────────────────────────────────

def _m1_setup(ws: str) -> None:
    _write(ws, "calculator.py", """
        def add(a, b):
            return a - b   # BUG: should be +

        def subtract(a, b):
            return a + b   # BUG: should be -

        def multiply(a, b):
            return a * b

        def divide(a, b):
            if b == 0:
                raise ValueError("division by zero")
            return a * b   # BUG: should be /
    """)
    _write(ws, "test_calculator.py", """
        from calculator import add, subtract, multiply, divide

        def test_add():
            assert add(2, 3) == 5

        def test_subtract():
            assert subtract(5, 3) == 2

        def test_divide():
            assert divide(10, 2) == 5.0
    """)


def _m1_check(ws: str) -> bool:
    p = Path(ws) / "calculator.py"
    if not p.exists():
        return False
    src = p.read_text()
    import re
    has_add = bool(re.search(r"return\s+a\s*\+\s*b", src))
    has_div = bool(re.search(r"return\s+a\s*/\s*b", src))
    return has_add and has_div


MISSION_1 = MissionDef(
    id="M1",
    name="Fix Bugs in Calculator",
    prompt=(
        "Fix all bugs in calculator.py. "
        "The add function returns a-b instead of a+b, "
        "subtract returns a+b instead of a-b, "
        "and divide returns a*b instead of a/b. "
        "Fix all three bugs."
    ),
    setup=_m1_setup,
    check=_m1_check,
    expected_min_files=1,
    category="fix",
)


# ── Mission 2: Create one React component ─────────────────────────────────────

def _m2_setup(ws: str) -> None:
    _write(ws, "package.json", """
        {
          "name": "benchmark-m2",
          "version": "1.0.0",
          "dependencies": { "react": "^18.0.0" }
        }
    """)


def _m2_check(ws: str) -> bool:
    # Accept any .jsx/.tsx/.js/.ts with JSX content and a function/component definition
    import re as _re
    _component_re = _re.compile(r'\bfunction\b|\bconst\s+\w+\s*=\s*(?:\([^)]*\)|[A-Za-z_]\w*)\s*=>')
    for ext in ("jsx", "tsx", "js", "ts"):
        for f in Path(ws).rglob(f"*.{ext}"):
            src = f.read_text(errors="ignore")
            if _component_re.search(src) and "return" in src and "<" in src:
                return True
    return False


MISSION_2 = MissionDef(
    id="M2",
    name="Create React Button Component",
    prompt=(
        "Create a reusable React Button component in src/components/Button.jsx. "
        "It should accept props: label (string), onClick (function), variant (primary/secondary/danger). "
        "Each variant should apply different CSS classes. Export as default."
    ),
    setup=_m2_setup,
    check=_m2_check,
    expected_min_files=1,
    category="build",
)


# ── Mission 3: Build JWT authentication ───────────────────────────────────────

def _m3_setup(ws: str) -> None:
    _write(ws, "requirements.txt", "fastapi\npyjwt\npython-multipart\n")
    _write(ws, "main.py", "# JWT auth API — to be implemented\n")


def _m3_check(ws: str) -> bool:
    for f in Path(ws).rglob("*.py"):
        src = f.read_text(errors="ignore").lower()
        if "jwt" in src and ("token" in src or "bearer" in src):
            return True
    return False


MISSION_3 = MissionDef(
    id="M3",
    name="Build JWT Authentication",
    prompt=(
        "Build a JWT authentication system in Python. "
        "Create auth.py with: create_access_token(user_id, secret), "
        "verify_token(token, secret) → user_id or None, "
        "and a /login and /me FastAPI route in main.py. "
        "Use PyJWT. Include requirements.txt."
    ),
    setup=_m3_setup,
    check=_m3_check,
    expected_min_files=2,
    category="build",
)


# ── Mission 4: Create CRUD API ────────────────────────────────────────────────

def _m4_setup(ws: str) -> None:
    _write(ws, "requirements.txt", "fastapi\nuvicorn\npydantic\n")


def _m4_check(ws: str) -> bool:
    for f in Path(ws).rglob("*.py"):
        src = f.read_text(errors="ignore").lower()
        if all(method in src for method in ("get", "post", "put", "delete")):
            return True
    return False


MISSION_4 = MissionDef(
    id="M4",
    name="Create CRUD API",
    prompt=(
        "Build a complete CRUD REST API for a Todo item using FastAPI. "
        "Create models.py (Pydantic models: TodoCreate, TodoUpdate, TodoResponse), "
        "crud.py (in-memory storage with create/read/update/delete), "
        "and main.py (routes: POST /todos, GET /todos, GET /todos/{id}, "
        "PUT /todos/{id}, DELETE /todos/{id}). "
        "Include proper HTTP status codes."
    ),
    setup=_m4_setup,
    check=_m4_check,
    expected_min_files=3,
    category="build",
)


# ── Mission 5: Fix 50 injected bugs ──────────────────────────────────────────

_BUGGY_TEMPLATE = '''
def process_items(items):
    result = []
    for i in range(len(items)):
        if items[i] > 0:
            result.append(items[i] * 2)  # BUG_{n}: should multiply by 3
    return result


def calculate_score(values):
    if len(values) == 0:
        return 0  # BUG_{n2}: should return -1 for empty
    total = sum(values)
    return total / len(values)  # correct


def is_valid_email(email):
    return "@" in email and "." not in email  # BUG_{n3}: should be "." in email


'''


def _m5_setup(ws: str) -> None:
    # Create 5 Python files each with ~10 bugs
    for i in range(1, 6):
        content = f"# Module {i} — contains intentional bugs\n"
        content += "from typing import List\n\n"
        for j in range(1, 11):
            n = (i - 1) * 10 + j
            content += f"# BUG_{n}: intentional error below\n"
            content += f"def func_{n}(x, y):\n"
            if n % 3 == 0:
                content += f"    return x - y  # BUG: should be x + y\n\n"
            elif n % 3 == 1:
                content += f"    return x * y  # BUG: should be x / y if y else 0\n\n"
            else:
                content += f"    return x ** y  # BUG: should be x % y\n\n"
        _write(ws, f"module_{i}.py", content)
    _write(ws, "README.md",
           "# Buggy App\nThis project has 50 intentional bugs spread across 5 modules.\n"
           "Each bug is marked with `# BUG_N:`.\n")


def _m5_check(ws: str) -> bool:
    # Check that at least 3 of 5 modules were modified
    fixed = 0
    for i in range(1, 6):
        p = Path(ws) / f"module_{i}.py"
        if p.exists():
            src = p.read_text()
            # A fix would remove the BUG comment or change the operation
            if "x + y" in src or "x / y" in src or "x % y" in src:
                fixed += 1
    return fixed >= 3


MISSION_5 = MissionDef(
    id="M5",
    name="Fix 50 Injected Bugs",
    prompt=(
        "Fix all bugs in this project. "
        "There are 50 intentional bugs spread across module_1.py through module_5.py. "
        "Each bug is marked with a comment '# BUG_N:'. "
        "For each bug: func_N where N%3==0 should return x+y, "
        "N%3==1 should return x/y (or 0 if y==0), N%3==2 should return x%y. "
        "Fix all 50 functions."
    ),
    setup=_m5_setup,
    check=_m5_check,
    expected_min_files=5,
    category="fix",
)


# ── Mission 6: Refactor medium project ───────────────────────────────────────

def _m6_setup(ws: str) -> None:
    # A monolithic script to refactor into modules
    _write(ws, "app.py", '''
        import json
        import hashlib
        import datetime

        # User management — all in one file, needs refactoring
        users_db = {}

        def create_user(username, password, email):
            if username in users_db:
                return {"error": "user exists"}
            hashed = hashlib.sha256(password.encode()).hexdigest()
            users_db[username] = {
                "username": username,
                "password": hashed,
                "email": email,
                "created": str(datetime.datetime.now()),
                "active": True,
            }
            return {"ok": True, "username": username}

        def login(username, password):
            user = users_db.get(username)
            if not user:
                return {"error": "not found"}
            hashed = hashlib.sha256(password.encode()).hexdigest()
            if user["password"] != hashed:
                return {"error": "wrong password"}
            return {"ok": True, "token": f"token_{username}"}

        def get_profile(username):
            user = users_db.get(username)
            if not user:
                return {"error": "not found"}
            return {k: v for k, v in user.items() if k != "password"}

        def deactivate_user(username):
            if username not in users_db:
                return {"error": "not found"}
            users_db[username]["active"] = False
            return {"ok": True}

        def list_active_users():
            return [u for u, data in users_db.items() if data.get("active")]

        # Order management
        orders_db = {}
        order_counter = 0

        def create_order(username, items, total):
            global order_counter
            order_counter += 1
            order_id = f"ORD-{order_counter:04d}"
            orders_db[order_id] = {
                "id": order_id,
                "username": username,
                "items": items,
                "total": total,
                "status": "pending",
                "created": str(datetime.datetime.now()),
            }
            return {"ok": True, "order_id": order_id}

        def get_order(order_id):
            return orders_db.get(order_id, {"error": "not found"})

        def update_order_status(order_id, status):
            if order_id not in orders_db:
                return {"error": "not found"}
            orders_db[order_id]["status"] = status
            return {"ok": True}

        def get_user_orders(username):
            return [o for o in orders_db.values() if o["username"] == username]
    ''')
    _write(ws, "requirements.txt", "# no dependencies\n")


def _m6_check(ws: str) -> bool:
    # Should have at least 2 separate module files beyond app.py
    py_files = list(Path(ws).rglob("*.py"))
    return len(py_files) >= 3


MISSION_6 = MissionDef(
    id="M6",
    name="Refactor Monolith into Modules",
    prompt=(
        "Refactor app.py into a clean modular structure. "
        "Split into: users.py (all user management functions), "
        "orders.py (all order management functions), "
        "database.py (the in-memory storage dicts), "
        "and update app.py to import from the modules. "
        "Keep all function signatures identical."
    ),
    setup=_m6_setup,
    check=_m6_check,
    expected_min_files=4,
    category="refactor",
)


# ── Mission 7: Full-stack mini application ────────────────────────────────────

def _m7_setup(ws: str) -> None:
    _write(ws, "README.md",
           "# Task Manager App\nBuild a full-stack task manager.\n")


def _m7_check(ws: str) -> bool:
    has_frontend = any(
        (Path(ws) / d).exists()
        for d in ("frontend", "src", "public", "client")
    ) or any(Path(ws).rglob("*.html"))
    has_backend = any(Path(ws).rglob("*.py")) or any(Path(ws).rglob("*.js"))
    has_multiple_files = len(list(Path(ws).rglob("*.*"))) >= 4
    return has_frontend and has_backend and has_multiple_files


MISSION_7 = MissionDef(
    id="M7",
    name="Full-Stack Task Manager",
    prompt=(
        "Build a simple full-stack Task Manager application. "
        "Backend: Python FastAPI with endpoints: "
        "POST /tasks, GET /tasks, PUT /tasks/{id}, DELETE /tasks/{id}. "
        "Frontend: A single HTML page (index.html) with vanilla JS that "
        "calls the API, shows tasks in a list, and has add/complete/delete buttons. "
        "Include a requirements.txt."
    ),
    setup=_m7_setup,
    check=_m7_check,
    expected_min_files=3,
    category="build",
)


# ── Mission 8: Large repository modification ──────────────────────────────────

def _m8_setup(ws: str) -> None:
    # Seed a small but multi-file project to modify
    for i in range(1, 8):
        _write(ws, f"src/module_{i}.py", f'''
            """Module {i}."""

            class Service{i}:
                """Service {i}."""

                def process(self, data):
                    return data

                def validate(self, data):
                    return bool(data)

                def transform(self, data):
                    return str(data)
        ''')
    _write(ws, "src/__init__.py", "\n".join(
        f"from .module_{i} import Service{i}" for i in range(1, 8)
    ) + "\n")
    _write(ws, "requirements.txt", "# no dependencies\n")
    _write(ws, "README.md",
           "# Multi-module project\nAdd logging to all service methods.\n")


def _m8_check(ws: str) -> bool:
    logged = 0
    for i in range(1, 8):
        p = Path(ws) / f"src/module_{i}.py"
        if p.exists():
            src = p.read_text()
            if "logging" in src or "logger" in src or "print(" in src:
                logged += 1
    return logged >= 5  # at least 5 of 7 modules should have logging


MISSION_8 = MissionDef(
    id="M8",
    name="Add Logging to All Modules",
    prompt=(
        "Add Python logging to all 7 service modules in src/. "
        "Each module should: import logging, create a logger with "
        "logging.getLogger(__name__), and add a debug log line at the "
        "start of each method (process, validate, transform). "
        "Do not change any existing logic."
    ),
    setup=_m8_setup,
    check=_m8_check,
    expected_min_files=7,
    category="modify",
)


# ── Mission registry ──────────────────────────────────────────────────────────

ALL_MISSIONS: list[MissionDef] = [
    MISSION_1,
    MISSION_2,
    MISSION_3,
    MISSION_4,
    MISSION_5,
    MISSION_6,
    MISSION_7,
    MISSION_8,
]

MISSION_BY_ID: dict[str, MissionDef] = {m.id: m for m in ALL_MISSIONS}
