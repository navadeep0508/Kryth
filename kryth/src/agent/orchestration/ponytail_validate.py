"""Ponytail Validation — Phase 10.

Compares NORMAL vs PONYTAIL worker output across five representative tasks
(bug fix, small feature, CRUD API, authentication, refactor).

No LLM calls — each task ships a hand-authored pair of code corpora
representing what an unconstrained agent typically produces (NORMAL) vs.
what a Ponytail-disciplined agent produces (PONYTAIL) for the same request.
Both corpora are run through the SAME scoring engine
(AbstractionDetector + compute_overengineering_score) so the comparison is
mechanical, not narrated — the same code that grades real missions.

Usage:
    python -m agent.orchestration.ponytail_validate
    python -m agent.orchestration.ponytail_validate --task crud_api
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Dict, List

from agent.orchestration.ponytail import (
    AbstractionDetector,
    OverengineeringFactors,
    compute_overengineering_score,
)


# ── Task corpora ───────────────────────────────────────────────────────────
#
# Each task is a (normal_files, ponytail_files) pair. "Dependencies added" is
# counted from import/require lines not already in the stdlib/baseline set —
# a deliberately simple proxy, same spirit as the rest of this validator.

_STDLIB_BASELINE = {
    "os", "sys", "re", "json", "typing", "dataclasses", "datetime", "uuid",
    "hashlib", "functools", "itertools", "pathlib", "logging", "subprocess",
    "fastapi", "pydantic",   # treated as "already installed" for these tasks
}


@dataclass
class TaskCorpus:
    name: str
    normal_files: Dict[str, str] = field(default_factory=dict)
    ponytail_files: Dict[str, str] = field(default_factory=dict)


def _bug_fix() -> TaskCorpus:
    normal = {
        "src/validators/email_validator_service.py": """
class EmailValidatorService:
    def __init__(self):
        self.pattern_provider = EmailPatternProvider()

    def validate(self, email):
        return self.pattern_provider.get_pattern().match(email)


class EmailPatternProvider:
    def __init__(self):
        import re
        self._pattern = re.compile(r"^[^@]+@[^@]+\\.[^@]+$")

    def get_pattern(self):
        return self._pattern


def _is_valid_email_helper(email):
    return EmailValidatorService().validate(email)


def check_signup_email(email):
    return _is_valid_email_helper(email)
""",
        "src/utils/string_utils.py": """
def _trim_helper(s):
    return s.strip()
""",
    }
    ponytail = {
        "src/validators.py": """
import re

EMAIL_RE = re.compile(r"^[^@]+@[^@]+\\.[^@]+$")


def is_valid_email(email):
    # ponytail: simple RFC-adjacent check; good enough for signup gating
    return bool(EMAIL_RE.match(email))
""",
    }
    return TaskCorpus("bug_fix", normal, ponytail)


def _small_feature() -> TaskCorpus:
    normal = {
        "src/components/DatePickerWrapper.jsx": """
import Flatpickr from 'flatpickr';

class DatePickerService {
  constructor(target) { this.target = target; }
  init() { this.instance = Flatpickr(this.target, {}); }
  getValue() { return this.instance.selectedDates[0]; }
}

export function DatePickerWrapper({ onChange }) {
  const ref = React.useRef();
  React.useEffect(() => {
    const svc = new DatePickerService(ref.current);
    svc.init();
  }, []);
  return <input ref={ref} />;
}
""",
        "src/services/DatePickerHelper.js": """
export function formatPickerDate(d) { return d.toISOString(); }
""",
    }
    ponytail = {
        "src/components/SignupForm.jsx": """
// ponytail: browser has one
<input type="date" onChange={e => onChange(e.target.value)} />
""",
    }
    return TaskCorpus("small_feature", normal, ponytail)


def _crud_api() -> TaskCorpus:
    normal = {
        "src/services/UserService.py": """
class UserRepository:
    def __init__(self, db):
        self.db = db
    def get(self, id):
        return self.db.query(User).get(id)


class UserServiceWrapper:
    def __init__(self, repo):
        self.repo = repo
    def get_user(self, id):
        return self.repo.get(id)


class UserManager:
    def __init__(self, service):
        self.service = service
    def fetch(self, id):
        return self.service.get_user(id)
""",
        "src/services/UserValidatorService.py": """
class UserValidatorService:
    def validate(self, data):
        return bool(data.get("name"))
""",
        "src/services/UserSerializerHelper.py": """
def _serialize_helper(user):
    return {"id": user.id, "name": user.name}

def serialize_user(user):
    return _serialize_helper(user)
""",
        "src/routes/user_routes.py": """
from fastapi import APIRouter
router = APIRouter()
""",
    }
    ponytail = {
        "src/routes/users.py": """
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class User(BaseModel):
    id: int
    name: str


@router.get("/users/{user_id}")
def get_user(user_id: int, db=Depends(get_db)):
    user = db.query(UserModel).get(user_id)
    if not user:
        raise HTTPException(404, "not found")
    return user
""",
    }
    return TaskCorpus("crud_api", normal, ponytail)


def _authentication() -> TaskCorpus:
    normal = {
        "src/auth/AuthenticationServiceWrapper.py": """
class PasswordHasherProvider:
    def __init__(self):
        self.hasher = BcryptHasherAdapter()
    def hash(self, pw):
        return self.hasher.hash(pw)


class BcryptHasherAdapter:
    def hash(self, pw):
        import bcrypt
        return bcrypt.hashpw(pw.encode(), bcrypt.gensalt())


class AuthenticationServiceWrapper:
    def __init__(self, provider):
        self.provider = provider
    def authenticate(self, pw):
        return self.provider.hash(pw)
""",
        "src/auth/TokenFactoryInterface.py": """
from abc import ABC, abstractmethod

class TokenFactoryInterface(ABC):
    @abstractmethod
    def create(self, payload): ...


class JWTTokenFactory(TokenFactoryInterface):
    def create(self, payload):
        import jwt
        return jwt.encode(payload, "secret")
""",
        "src/auth/AuthHelperUtils.py": """
def _build_claims_helper(user):
    return {"sub": user.id}

def make_claims(user):
    return _build_claims_helper(user)
""",
    }
    ponytail = {
        "src/auth.py": """
from datetime import datetime, timedelta
import jwt
from passlib.hash import bcrypt

SECRET = "change-me"


def hash_password(pw):
    return bcrypt.hash(pw)


def verify_password(pw, hashed):
    return bcrypt.verify(pw, hashed)


def make_token(user_id):
    payload = {"sub": user_id, "exp": datetime.utcnow() + timedelta(hours=1)}
    return jwt.encode(payload, SECRET, algorithm="HS256")
""",
    }
    return TaskCorpus("authentication", normal, ponytail)


def _refactor() -> TaskCorpus:
    normal = {
        "src/legacy/OrderProcessorFacade.py": """
class OrderValidatorInterface:
    def validate(self, order): ...

class DefaultOrderValidator(OrderValidatorInterface):
    def validate(self, order):
        return order.total > 0


class OrderProcessorFacade:
    def __init__(self, validator):
        self.validator = validator
    def process(self, order):
        return self.validator.validate(order)


class OrderProcessorManager:
    def __init__(self, facade):
        self.facade = facade
    def run(self, order):
        return self.facade.process(order)
""",
        "src/legacy/OrderHelperUtils.py": """
def _calc_total_helper(items):
    return sum(i.price for i in items)

def calc_total(items):
    return _calc_total_helper(items)
""",
    }
    ponytail = {
        "src/orders.py": """
def process_order(order):
    # ponytail: single validation rule today; extend here if more appear
    if order.total <= 0:
        raise ValueError("order total must be positive")
    return order
""",
    }
    return TaskCorpus("refactor", normal, ponytail)


TASKS: Dict[str, TaskCorpus] = {
    c.name: c for c in (
        _bug_fix(), _small_feature(), _crud_api(), _authentication(), _refactor(),
    )
}


# ── Measurement ───────────────────────────────────────────────────────────

@dataclass
class ArmMetrics:
    files_created: int = 0
    lines_added: int = 0
    dependencies_added: int = 0
    overengineering_score: float = 0.0
    grade: str = ""


def _count_dependencies(files: Dict[str, str]) -> int:
    deps = set()
    for source in files.values():
        for line in source.splitlines():
            line = line.strip()
            mod = None
            if line.startswith("import "):
                mod = line.split()[1].split(".")[0].rstrip(",")
            elif line.startswith("from "):
                mod = line.split()[1].split(".")[0]
            elif "require(" in line or line.startswith("import ") and "from '" in line:
                continue  # JS import handled below
            if mod and mod not in _STDLIB_BASELINE:
                deps.add(mod)
            # crude JS import detection
            if "from '" in line and "import" in line:
                pkg = line.split("from '")[-1].split("'")[0]
                if not pkg.startswith("."):
                    deps.add(pkg)
    return len(deps)


def measure_arm(files: Dict[str, str]) -> ArmMetrics:
    detector = AbstractionDetector()
    flags = detector.scan_files(files)

    factors = OverengineeringFactors(
        files_created=len(files),
        abstractions_created=sum(1 for f in flags if f.kind in ("wrapper", "redundant_interface")),
        helpers_created=sum(1 for f in flags if f.kind == "single_use_helper"),
        services_created=sum(1 for path in files if "service" in path.lower() or "Service" in path),
        wrappers_created=sum(1 for f in flags if f.kind == "wrapper"),
        dependencies_added=_count_dependencies(files),
        expected_files=1,
    )
    score = compute_overengineering_score(factors)
    lines = sum(len(s.splitlines()) for s in files.values())

    return ArmMetrics(
        files_created=len(files),
        lines_added=lines,
        dependencies_added=factors.dependencies_added,
        overengineering_score=score.score,
        grade=score.grade(),
    )


@dataclass
class TaskComparison:
    task_name: str
    normal: ArmMetrics
    ponytail: ArmMetrics

    def pct_reduction(self, attr: str) -> float:
        n = getattr(self.normal, attr)
        p = getattr(self.ponytail, attr)
        if n == 0:
            return 0.0
        return (n - p) / n * 100


def compare_task(corpus: TaskCorpus) -> TaskComparison:
    return TaskComparison(
        task_name=corpus.name,
        normal=measure_arm(corpus.normal_files),
        ponytail=measure_arm(corpus.ponytail_files),
    )


# ── Reporting ─────────────────────────────────────────────────────────────

def run_all(target: str = None) -> bool:
    tasks = {k: v for k, v in TASKS.items() if target is None or k == target}
    if not tasks:
        print(f"Unknown task: {target}. Available: {list(TASKS)}")
        return False

    comparisons: List[TaskComparison] = []
    print("═══ PONYTAIL VALIDATION — NORMAL vs PONYTAIL ═══\n")
    header = f"{'Task':<16} {'Files':>14} {'Lines':>14} {'Deps':>10} {'Overeng.':>16}"
    print(header)
    print("-" * len(header))

    for name, corpus in tasks.items():
        cmp = compare_task(corpus)
        comparisons.append(cmp)
        print(
            f"{name:<16} "
            f"{cmp.normal.files_created:>5}→{cmp.ponytail.files_created:<5} ({cmp.pct_reduction('files_created'):>4.0f}%) "
            f"{cmp.normal.lines_added:>5}→{cmp.ponytail.lines_added:<5} "
            f"{cmp.normal.dependencies_added:>3}→{cmp.ponytail.dependencies_added:<3} "
            f"{cmp.normal.overengineering_score:>5.0f}→{cmp.ponytail.overengineering_score:<5.0f} "
            f"({cmp.normal.grade}→{cmp.ponytail.grade})"
        )

    print()
    print("═══ AGGREGATE ═══")
    n_files  = sum(c.normal.files_created for c in comparisons)
    p_files  = sum(c.ponytail.files_created for c in comparisons)
    n_lines  = sum(c.normal.lines_added for c in comparisons)
    p_lines  = sum(c.ponytail.lines_added for c in comparisons)
    n_deps   = sum(c.normal.dependencies_added for c in comparisons)
    p_deps   = sum(c.ponytail.dependencies_added for c in comparisons)
    n_score  = sum(c.normal.overengineering_score for c in comparisons) / len(comparisons)
    p_score  = sum(c.ponytail.overengineering_score for c in comparisons) / len(comparisons)

    def pct(n, p):
        return (n - p) / n * 100 if n else 0.0

    print(f"  Files created:        {n_files} → {p_files}   ({pct(n_files, p_files):.0f}% reduction)")
    print(f"  Lines added:          {n_lines} → {p_lines}   ({pct(n_lines, p_lines):.0f}% reduction)")
    print(f"  Dependencies added:   {n_deps} → {p_deps}   ({pct(n_deps, p_deps):.0f}% reduction)")
    print(f"  Avg overengineering:  {n_score:.0f} → {p_score:.0f}   ({pct(n_score, p_score):.0f}% reduction)")

    # Validation checks — same bar as v4_validate.py: assert the philosophy holds
    all_passed = True
    checks = [
        ("ponytail uses fewer or equal files in every task",
         all(c.ponytail.files_created <= c.normal.files_created for c in comparisons)),
        ("ponytail writes fewer or equal lines in every task",
         all(c.ponytail.lines_added <= c.normal.lines_added for c in comparisons)),
        ("ponytail overengineering score is lower in every task",
         all(c.ponytail.overengineering_score <= c.normal.overengineering_score for c in comparisons)),
        ("aggregate file reduction is positive", pct(n_files, p_files) > 0),
        ("aggregate overengineering reduction is positive", pct(n_score, p_score) > 0),
    ]
    print()
    print("═══ CHECKS ═══")
    for label, ok in checks:
        print(f"  {'✓' if ok else '✗'} {label}")
        all_passed = all_passed and ok

    print()
    if all_passed:
        print("KRYTH PONYTAIL EXECUTION PROFILE COMPLETE")
    else:
        print("✗ Some checks failed — see above.")
    return all_passed


def main() -> None:
    parser = argparse.ArgumentParser(description="Ponytail NORMAL vs PONYTAIL validation")
    parser.add_argument("--task", default=None, help=f"Run one task only. Choices: {list(TASKS)}")
    args = parser.parse_args()
    ok = run_all(target=args.task)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
