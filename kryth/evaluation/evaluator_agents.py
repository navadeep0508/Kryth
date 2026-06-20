"""Eight reviewer agents that inspect a workspace and produce dimension scores.

Each agent has:
  - a scoring prompt (used if LLM is available)
  - a static fallback scorer (always works)
  - runs in its own thread

Reviewer agents NEVER modify code — read-only inspection only.
"""

from __future__ import annotations

import ast
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from pathlib import Path
from typing import Optional

from .evaluation_metrics import ReviewScore


# ── LLM call helper (optional) ───────────────────────────────────────────────

def _llm_score(prompt: str, timeout: float = 45.0) -> Optional[dict]:
    """Try a single LLM call. Returns parsed JSON dict or None on failure."""
    result_holder: list[Optional[dict]] = [None]
    done = threading.Event()

    def _call():
        try:
            import sys
            _kryth_src = str(
                (Path(__file__).parent.parent / "kryth" / "src").resolve()
            )
            if _kryth_src not in sys.path:
                sys.path.insert(0, _kryth_src)

            from agent.llm import ask_llm_stream
            chunks: list[str] = []
            for chunk in ask_llm_stream(
                messages=[{"role": "user", "content": prompt}],
                system=(
                    "You are a code reviewer. Respond ONLY with a valid JSON object. "
                    "No markdown fences, no explanation — only JSON."
                ),
                stream=True,
            ):
                if isinstance(chunk, str):
                    chunks.append(chunk)
            raw = "".join(chunks).strip()
            # Strip markdown fences if present
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            result_holder[0] = json.loads(raw)
        except Exception:
            pass
        finally:
            done.set()

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    done.wait(timeout=timeout)
    return result_holder[0]


def _clamp(v, lo=0, hi=100) -> int:
    return max(lo, min(hi, int(v)))


# ── File reader ───────────────────────────────────────────────────────────────

_SKIP_DIRS = frozenset(
    {"node_modules", ".git", "__pycache__", ".venv", "venv",
     "dist", "build", ".mypy_cache", ".pytest_cache"}
)
_CODE_EXTS = frozenset(
    {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css",
     ".json", ".yaml", ".yml", ".md", ".sh"}
)
_MAX_FILE_BYTES = 40_000  # read up to 40KB per file for LLM context


def _collect_files(workspace: str) -> list[Path]:
    ws = Path(workspace)
    result = []
    for root, dirs, files in os.walk(ws):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() in _CODE_EXTS:
                result.append(p)
    return result[:40]  # limit to 40 files


def _read_files(files: list[Path], limit_bytes: int = _MAX_FILE_BYTES) -> str:
    parts = []
    total = 0
    for p in files:
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            if total + len(content) > 60_000:
                content = content[:max(0, 60_000 - total)]
            parts.append(f"\n# === {p.name} ===\n{content}")
            total += len(content)
            if total >= 60_000:
                break
        except OSError:
            pass
    return "\n".join(parts)


def _count_test_files(workspace: str) -> int:
    ws = Path(workspace)
    count = 0
    for p in ws.rglob("*"):
        n = p.name.lower()
        if n.startswith("test_") or n.endswith("_test.py") or ".test." in n or ".spec." in n:
            count += 1
    return count


# ── Base reviewer ─────────────────────────────────────────────────────────────

class ReviewerAgent:
    name: str = "base"
    dimension: str = "base"
    weight: float = 1.0
    _PROMPT_TEMPLATE: str = ""

    def _static_score(self, workspace: str, files: list[Path]) -> ReviewScore:
        return ReviewScore(dimension=self.dimension, score=60, reviewer="static")

    def _llm_score(self, workspace: str, files: list[Path]) -> Optional[ReviewScore]:
        if not self._PROMPT_TEMPLATE:
            return None
        code = _read_files(files)
        if not code.strip():
            return None
        prompt = self._PROMPT_TEMPLATE.replace("{CODE}", code[:55_000])
        result = _llm_score(prompt)
        if result is None:
            return None
        try:
            score = _clamp(result.get("score", 50))
            findings = result.get("findings", [])[:10]
            suggestions = result.get("suggestions", [])[:5]
            return ReviewScore(
                dimension=self.dimension,
                score=score,
                weight=self.weight,
                findings=[str(f) for f in findings],
                suggestions=[str(s) for s in suggestions],
                reviewer="llm",
            )
        except Exception:
            return None

    def review(self, workspace: str, timeout: float = 45.0) -> ReviewScore:
        t0 = time.monotonic()
        files = _collect_files(workspace)
        rs = self._llm_score(workspace, files)
        if rs is None:
            rs = self._static_score(workspace, files)
        rs.weight = self.weight
        rs.duration_s = time.monotonic() - t0
        return rs


# ── Architecture Reviewer ─────────────────────────────────────────────────────

class ArchitectureReviewer(ReviewerAgent):
    name = "Architecture Reviewer"
    dimension = "architecture"
    weight = 1.0

    _PROMPT_TEMPLATE = """
Review the code architecture. Score 0-100 based on:
- Separation of concerns (files/modules with single responsibilities)
- Appropriate abstraction (not too deep, not flat)
- Clean dependency direction (no circular imports)
- Proper package/module structure
- Appropriate use of design patterns

Files to review:
{CODE}

Respond ONLY with JSON:
{{"score": <0-100>, "findings": ["...", "..."], "suggestions": ["...", "..."]}}
"""

    def _static_score(self, workspace: str, files: list[Path]) -> ReviewScore:
        ws = Path(workspace)
        score = 65
        findings = []
        suggestions = []

        py_files = [f for f in files if f.suffix == ".py"]
        n_files = len(py_files)

        if n_files == 0:
            # Non-Python project — check JS structure
            js_files = [f for f in files if f.suffix in (".js", ".jsx", ".ts", ".tsx")]
            if not js_files:
                return ReviewScore(dimension=self.dimension, score=50,
                                   reviewer="static", findings=["No source files found"])
            n_files = len(js_files)

        # Single-file project
        if n_files == 1:
            score = 55
            findings.append("All code in one file — poor separation of concerns")
            suggestions.append("Split into modules by responsibility")
        elif n_files >= 3:
            score += 10
            findings.append(f"Good modular structure ({n_files} source files)")

        # Has __init__.py
        if list(ws.rglob("__init__.py")):
            score += 5

        # Naming conventions (models/routes/crud/service)
        names = {f.stem.lower() for f in files}
        good_names = {"models", "routes", "crud", "service", "handler",
                      "controller", "utils", "config", "schema"}
        matched = good_names & names
        if len(matched) >= 2:
            score += 5
            findings.append(f"Good naming conventions: {sorted(matched)}")

        return ReviewScore(
            dimension=self.dimension, score=_clamp(score),
            findings=findings, suggestions=suggestions, reviewer="static",
        )


# ── Code Quality Reviewer ─────────────────────────────────────────────────────

class CodeQualityReviewer(ReviewerAgent):
    name = "Code Quality Reviewer"
    dimension = "code_quality"
    weight = 1.0

    _PROMPT_TEMPLATE = """
Review code quality. Score 0-100 based on:
- Clean readable code
- Meaningful variable/function names
- No dead code or unused variables
- No duplicated logic
- Appropriate error handling
- Consistent style

Files:
{CODE}

Respond ONLY with JSON:
{{"score": <0-100>, "findings": ["...", "..."], "suggestions": ["...", "..."]}}
"""

    def _static_score(self, workspace: str, files: list[Path]) -> ReviewScore:
        # quality_rules.py already does a thorough analysis
        # here we produce a complementary LLM-independent score
        from .quality_rules import analyze_workspace
        viols, rewards, score = analyze_workspace(workspace)
        findings = [f"{v.severity.upper()}: {v.message}" for v in viols[:8]]
        suggestions = [r.message for r in rewards[:5]]
        return ReviewScore(
            dimension=self.dimension, score=score,
            findings=findings, suggestions=suggestions, reviewer="static",
        )


# ── Testing Reviewer ──────────────────────────────────────────────────────────

class TestingReviewer(ReviewerAgent):
    name = "Testing Reviewer"
    dimension = "testing"
    weight = 1.0

    _PROMPT_TEMPLATE = """
Review test quality. Score 0-100 based on:
- Presence and coverage of tests
- Test for happy path AND edge cases
- Test naming clarity
- Proper use of assertions
- Absence of brittle or redundant tests

Files:
{CODE}

Respond ONLY with JSON:
{{"score": <0-100>, "findings": ["...", "..."], "suggestions": ["...", "..."]}}
"""

    def _static_score(self, workspace: str, files: list[Path]) -> ReviewScore:
        ws = Path(workspace)
        score = 40
        findings = []
        suggestions = []

        test_count = _count_test_files(workspace)
        src_py = [f for f in files if f.suffix == ".py" and "test" not in f.name.lower()]

        if test_count == 0:
            findings.append("No test files found")
            suggestions.append("Add unit tests for all public functions")
            score = 20
        else:
            ratio = test_count / max(len(src_py), 1)
            score = min(85, 40 + int(ratio * 40) + test_count * 3)
            findings.append(f"{test_count} test file(s) found")

        # Check for assertions in test files
        assert_count = 0
        for p in ws.rglob("test_*.py"):
            try:
                src = p.read_text(errors="ignore")
                assert_count += src.count("assert ")
                assert_count += src.count(".assertEqual(")
                assert_count += src.count(".assertTrue(")
            except Exception:
                pass
        if assert_count > 5:
            score = min(90, score + 5)
            findings.append(f"{assert_count} assertions found in tests")

        return ReviewScore(
            dimension=self.dimension, score=_clamp(score),
            findings=findings, suggestions=suggestions, reviewer="static",
        )


# ── Performance Reviewer ──────────────────────────────────────────────────────

class PerformanceReviewer(ReviewerAgent):
    name = "Performance Reviewer"
    dimension = "performance"
    weight = 1.0

    _PROMPT_TEMPLATE = """
Review code for performance issues. Score 0-100 based on:
- Algorithmic efficiency (avoid O(n²) when O(n) possible)
- No unnecessary re-computation inside loops
- Appropriate use of data structures (dict vs list lookups)
- No blocking I/O in async code
- Memory efficiency

Files:
{CODE}

Respond ONLY with JSON:
{{"score": <0-100>, "findings": ["...", "..."], "suggestions": ["...", "..."]}}
"""

    _NESTED_LOOP = re.compile(
        r'for .+ in .+:\s*\n\s+for .+ in .+:', re.MULTILINE
    )
    _SLOW_PATTERNS = [
        (re.compile(r'\.find\(.*\).*in\s+loop', re.IGNORECASE), "find() inside loop"),
        (re.compile(r'for .+ in range\(len\('), "range(len(x)) — use enumerate"),
        (re.compile(r'\+= .+ in .+for'), "string concatenation in loop"),
    ]

    def _static_score(self, workspace: str, files: list[Path]) -> ReviewScore:
        score = 70
        findings = []
        suggestions = []

        for p in files:
            if p.suffix not in (".py", ".js", ".ts"):
                continue
            try:
                src = p.read_text(errors="ignore")
            except OSError:
                continue

            if self._NESTED_LOOP.search(src):
                score -= 5
                findings.append(f"{p.name}: nested loop detected (check O(n²) risk)")

            for pattern, msg in self._SLOW_PATTERNS:
                if pattern.search(src):
                    score -= 3
                    findings.append(f"{p.name}: {msg}")

            # Reward async usage
            if "async def" in src or "asyncio" in src or "await " in src:
                score = min(score + 5, 85)
                findings.append(f"{p.name}: uses async I/O")

        return ReviewScore(
            dimension=self.dimension, score=_clamp(score),
            findings=findings, suggestions=suggestions, reviewer="static",
        )


# ── Security Reviewer ─────────────────────────────────────────────────────────

class SecurityReviewer(ReviewerAgent):
    name = "Security Reviewer"
    dimension = "security"
    weight = 1.0

    _PROMPT_TEMPLATE = """
Review code for security vulnerabilities. Score 0-100 based on:
- No hardcoded secrets or credentials
- No SQL injection risk (parameterized queries)
- No command injection (no shell=True with user input)
- Input validation at API boundaries
- No insecure random for security-sensitive ops
- No path traversal risks

Files:
{CODE}

Respond ONLY with JSON:
{{"score": <0-100>, "findings": ["...", "..."], "suggestions": ["...", "..."]}}
"""

    _RISKY = [
        (re.compile(r'shell\s*=\s*True'), "shell=True — injection risk", 15),
        (re.compile(r'eval\s*\('), "eval() — code injection risk", 20),
        (re.compile(r'exec\s*\('), "exec() — code injection risk", 20),
        (re.compile(r'pickle\.load'), "pickle.load — deserialization risk", 10),
        (re.compile(r'MD5\b|md5\('), "MD5 — use SHA-256+", 5),
        (re.compile(r'random\.random\(\)|random\.randint'), "random module not cryptographically secure", 3),
        (re.compile(r'f".*SELECT.*{'), "Possible SQL injection via f-string", 20),
        (re.compile(r'os\.system\('), "os.system — prefer subprocess with args list", 8),
    ]

    def _static_score(self, workspace: str, files: list[Path]) -> ReviewScore:
        from .quality_rules import security_score_from_violations, analyze_workspace
        viols, _, _ = analyze_workspace(workspace)
        base = security_score_from_violations(viols)

        findings = []
        penalty = 0

        for p in files:
            try:
                src = p.read_text(errors="ignore")
            except OSError:
                continue
            for pattern, msg, cost in self._RISKY:
                if pattern.search(src):
                    findings.append(f"{p.name}: {msg}")
                    penalty += cost

        score = _clamp(base - penalty)
        suggestions = []
        if penalty > 0:
            suggestions.append("Address security findings before production use")
        return ReviewScore(
            dimension=self.dimension, score=score,
            findings=findings, suggestions=suggestions, reviewer="static",
        )


# ── Maintainability Reviewer ──────────────────────────────────────────────────

class MaintainabilityReviewer(ReviewerAgent):
    name = "Maintainability Reviewer"
    dimension = "maintainability"
    weight = 1.0

    _PROMPT_TEMPLATE = """
Review code for maintainability. Score 0-100 based on:
- Code is easy to understand without context
- Functions have single responsibilities
- Minimal coupling between modules
- Configuration not hardcoded
- Easy to extend without modifying existing code

Files:
{CODE}

Respond ONLY with JSON:
{{"score": <0-100>, "findings": ["...", "..."], "suggestions": ["...", "..."]}}
"""

    def _static_score(self, workspace: str, files: list[Path]) -> ReviewScore:
        score = 70
        findings = []
        ws = Path(workspace)

        # Config in code?
        magic_numbers = 0
        long_fns = 0
        for p in files:
            if p.suffix != ".py":
                continue
            try:
                src = p.read_text(errors="ignore")
                tree = ast.parse(src)
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    length = (getattr(node, "end_lineno", node.lineno) or node.lineno) - node.lineno
                    if length > 50:
                        long_fns += 1
                if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                    if node.value not in (0, 1, -1, 2, 100, True, False):
                        magic_numbers += 1

        if long_fns > 2:
            score -= long_fns * 3
            findings.append(f"{long_fns} long functions (>50 lines) — reduce complexity")
        if magic_numbers > 5:
            score -= 5
            findings.append(f"{magic_numbers} magic numbers — use named constants")

        # Has config file?
        config_names = {"config.py", "settings.py", ".env.example", "config.yaml", "config.json"}
        existing_configs = [f for f in ws.rglob("*") if f.name in config_names]
        if existing_configs:
            score = min(score + 5, 90)
            findings.append(f"Configuration file found: {existing_configs[0].name}")

        return ReviewScore(
            dimension=self.dimension, score=_clamp(score),
            findings=findings, reviewer="static",
        )


# ── Documentation Reviewer ────────────────────────────────────────────────────

class DocumentationReviewer(ReviewerAgent):
    name = "Documentation Reviewer"
    dimension = "documentation"
    weight = 0.5

    _PROMPT_TEMPLATE = """
Review documentation quality. Score 0-100 based on:
- README presence and completeness
- Function/class docstrings
- Inline comments where complex
- API endpoint documentation
- Clear error messages

Files:
{CODE}

Respond ONLY with JSON:
{{"score": <0-100>, "findings": ["...", "..."], "suggestions": ["...", "..."]}}
"""

    def _static_score(self, workspace: str, files: list[Path]) -> ReviewScore:
        ws = Path(workspace)
        score = 50
        findings = []

        # README
        if (ws / "README.md").exists() or (ws / "README.rst").exists():
            score += 15
            readme = (ws / "README.md")
            if readme.exists():
                content = readme.read_text(errors="ignore")
                if len(content) > 200:
                    score += 5
                    findings.append("README.md is substantial")
                else:
                    findings.append("README.md is minimal")
        else:
            findings.append("No README found")

        # Docstrings in Python
        docstring_count = 0
        fn_count = 0
        for p in files:
            if p.suffix != ".py":
                continue
            try:
                tree = ast.parse(p.read_text(errors="ignore"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    fn_count += 1
                    if ast.get_docstring(node):
                        docstring_count += 1

        if fn_count > 0:
            ratio = docstring_count / fn_count
            score = min(score + int(ratio * 20), 95)
            findings.append(
                f"{docstring_count}/{fn_count} functions/classes have docstrings "
                f"({ratio*100:.0f}%)"
            )

        return ReviewScore(
            dimension=self.dimension, score=_clamp(score),
            findings=findings, reviewer="static",
        )


# ── Parallel Efficiency Reviewer ──────────────────────────────────────────────

class ParallelEfficiencyReviewer(ReviewerAgent):
    name = "Parallel Efficiency Reviewer"
    dimension = "parallel_efficiency"
    weight = 0.5

    def _static_score(self, workspace: str, files: list[Path]) -> ReviewScore:
        # This reviewer augments metrics already collected by the benchmark.
        # Without benchmark data, we do a static heuristic.
        findings = []
        score = 50

        uses_async = False
        uses_threads = False
        uses_mp = False

        for p in files:
            if p.suffix not in (".py", ".js", ".ts"):
                continue
            try:
                src = p.read_text(errors="ignore")
            except OSError:
                continue
            if "async def" in src or "asyncio" in src:
                uses_async = True
            if "ThreadPoolExecutor" in src or "threading.Thread" in src:
                uses_threads = True
            if "ProcessPoolExecutor" in src or "multiprocessing" in src:
                uses_mp = True

        if uses_async:
            score += 20
            findings.append("Uses async/await for I/O concurrency")
        if uses_threads:
            score += 10
            findings.append("Uses threading for parallelism")
        if uses_mp:
            score += 10
            findings.append("Uses multiprocessing for CPU parallelism")
        if not (uses_async or uses_threads or uses_mp):
            findings.append("No parallelism detected — sequential only")

        return ReviewScore(
            dimension=self.dimension, score=_clamp(score),
            findings=findings, reviewer="static",
        )

    def review_with_benchmark_data(
        self,
        workspace: str,
        parallel_efficiency_pct: float,
        max_batch_size: int,
    ) -> ReviewScore:
        """Enhanced review when benchmark parallel metrics are available."""
        score = int(parallel_efficiency_pct)
        findings = [
            f"Parallel efficiency: {parallel_efficiency_pct:.1f}%",
            f"Max batch size: {max_batch_size}",
        ]
        return ReviewScore(
            dimension=self.dimension,
            score=_clamp(score),
            weight=self.weight,
            findings=findings,
            reviewer="benchmark",
        )


# ── Registry and parallel runner ─────────────────────────────────────────────

ALL_REVIEWERS: list[ReviewerAgent] = [
    ArchitectureReviewer(),
    CodeQualityReviewer(),
    TestingReviewer(),
    PerformanceReviewer(),
    SecurityReviewer(),
    MaintainabilityReviewer(),
    DocumentationReviewer(),
    ParallelEfficiencyReviewer(),
]

REVIEWER_BY_DIMENSION: dict[str, ReviewerAgent] = {
    r.dimension: r for r in ALL_REVIEWERS
}


def run_all_reviewers(
    workspace: str,
    timeout_s: float = 60.0,
    use_llm: bool = True,
    benchmark_parallel_pct: float = 0.0,
    benchmark_max_batch: int = 0,
) -> dict[str, ReviewScore]:
    """Run all 8 reviewers in parallel threads. Returns dict[dimension → ReviewScore]."""
    results: dict[str, ReviewScore] = {}
    lock = threading.Lock()

    def _run_one(reviewer: ReviewerAgent) -> None:
        try:
            if isinstance(reviewer, ParallelEfficiencyReviewer) and benchmark_max_batch > 0:
                rs = reviewer.review_with_benchmark_data(
                    workspace, benchmark_parallel_pct, benchmark_max_batch
                )
            else:
                if not use_llm:
                    files = _collect_files(workspace)
                    rs = reviewer._static_score(workspace, files)
                    rs.weight = reviewer.weight
                else:
                    rs = reviewer.review(workspace, timeout=timeout_s * 0.8)
            with lock:
                results[reviewer.dimension] = rs
        except Exception as exc:
            with lock:
                results[reviewer.dimension] = ReviewScore(
                    dimension=reviewer.dimension,
                    score=50,
                    findings=[f"Reviewer error: {exc}"],
                    reviewer="error",
                )

    with ThreadPoolExecutor(max_workers=len(ALL_REVIEWERS)) as ex:
        futures = [ex.submit(_run_one, r) for r in ALL_REVIEWERS]
        for f in as_completed(futures, timeout=timeout_s):
            try:
                f.result()
            except Exception:
                pass

    return results
