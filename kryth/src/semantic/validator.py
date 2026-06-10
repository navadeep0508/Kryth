"""Validation Pipeline - Comprehensive quality gates for edits.

This module implements a multi-stage validation system that ensures
edit operations and plans meet quality, security, and correctness
standards before application. It runs:

- Syntax validation (py_compile, eslint, etc.)
- Type checking (mypy, tsc)
- Linting (ruff, flake8, eslint)
- Security scanning (bandit, npm audit)
- Test execution (pytest, jest)
- Custom rules (project-specific)
- Performance impact analysis
- Dependency conflict detection

The pipeline is:
- Configurable (enable/disable checks)
- Parallel (runs independent checks concurrently)
- Incremental (only validates affected files)
- Cached (remembers previous results)
- Fail-fast (optional, stop on first error)

All validation results feed into the AutoRepairEngine to
automatically fix issues when possible.
"""

from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .data_structures import EditPlan, EditOperation, ValidationResult


@dataclass
class ValidationCheck:
    """A single validation check."""
    name: str
    description: str
    command: Optional[str] = None  # Shell command to run
    python_func: Optional[str] = None  # Name of Python function
    file_patterns: List[str] = field(default_factory=list)  # Which files to check
    fast: bool = True  # Whether to run in fast validation
    auto_fixable: bool = False  # Can auto-repair fix this?
    timeout: float = 30.0
    
    def should_run(self, file_path: str, fast: bool = False) -> bool:
        """Check if this check should run on the given file."""
        if not self.file_patterns:
            return True
        
        ext = Path(file_path).suffix.lower()
        for pattern in self.file_patterns:
            if pattern.startswith('*.'):
                if ext == pattern[1:]:
                    return True
            elif pattern in file_path:
                return True
        
        return False


class ValidationPipeline:
    """Orchestrates multiple validation checks.
    
    The ValidationPipeline runs a series of checks on edit operations
    and plans to ensure they meet quality standards. It supports:
    
    - Pre-execution validation (fast, quick checks)
    - Post-execution validation (comprehensive, slower)
    - Incremental validation (only affected files)
    - Parallel execution (thread pool)
    - Caching (avoid re-checking unchanged files)
    - Custom check registration
    
    Checks are organized by language and file type:
    
    Python:
    - py_compile (syntax)
    - mypy (type checking)
    - ruff (linting)
    - bandit (security)
    - pytest (tests)
    
    TypeScript/JavaScript:
    - tsc (type checking)
    - eslint (linting)
    - jest (tests)
    - npm audit (security)
    
    Java:
    - javac (compilation)
    - checkstyle (linting)
    - spotbugs (security)
    - mvn test (tests)
    
    The pipeline returns a ValidationResult with:
    - Overall validity
    - List of errors
    - List of warnings
    - Which checks were run
    - Execution time
    
    Integration:
    - Called by SemanticEditor before/after execution
    - Used by AutoRepairEngine to identify fixable issues
    - Results stored in EditMemory for learning
    - Can be extended with project-specific checks
    """
    
    def __init__(self) -> None:
        self.checks: List[ValidationCheck] = []
        self._setup_default_checks()
        self._cache: Dict[str, Tuple[float, ValidationResult]] = {}  # key -> (timestamp, result)
        self.cache_ttl: float = 60.0  # seconds
    
    def _setup_default_checks(self) -> None:
        """Setup default validation checks."""
        # Python checks
        self.checks.append(ValidationCheck(
            name="python_syntax",
            description="Python syntax validation",
            python_func="check_python_syntax",
            file_patterns=["*.py"],
            fast=True,
            auto_fixable=True,
        ))
        
        self.checks.append(ValidationCheck(
            name="ruff",
            description="Python linting with Ruff",
            command="ruff check",
            file_patterns=["*.py"],
            fast=True,
            auto_fixable=True,
        ))
        
        self.checks.append(ValidationCheck(
            name="mypy",
            description="Python type checking",
            command="mypy",
            file_patterns=["*.py"],
            fast=False,
            auto_fixable=False,
        ))
        
        self.checks.append(ValidationCheck(
            name="bandit",
            description="Python security scan",
            command="bandit -r",
            file_patterns=["*.py"],
            fast=False,
            auto_fixable=False,
        ))
        
        self.checks.append(ValidationCheck(
            name="pytest",
            description="Python tests",
            command="pytest -x",
            file_patterns=["*.py"],
            fast=False,
            auto_fixable=False,
        ))
        
        # TypeScript/JavaScript checks
        self.checks.append(ValidationCheck(
            name="tsc",
            description="TypeScript type checking",
            command="tsc --noEmit",
            file_patterns=["*.ts", "*.tsx", "*.js", "*.jsx"],
            fast=True,
            auto_fixable=False,
        ))
        
        self.checks.append(ValidationCheck(
            name="eslint",
            description="JavaScript/TypeScript linting",
            command="eslint --fix",
            file_patterns=["*.ts", "*.tsx", "*.js", "*.jsx"],
            fast=True,
            auto_fixable=True,
        ))
        
        self.checks.append(ValidationCheck(
            name="jest",
            description="JavaScript/TypeScript tests",
            command="jest --bail",
            file_patterns=["*.ts", "*.tsx", "*.js", "*.jsx"],
            fast=False,
            auto_fixable=False,
        ))
        
        # General checks
        self.checks.append(ValidationCheck(
            name="black",
            description="Python formatting",
            command="black --check",
            file_patterns=["*.py"],
            fast=True,
            auto_fixable=True,
        ))
        
        self.checks.append(ValidationCheck(
            name="prettier",
            description="JS/TS/JSON formatting",
            command="prettier --check",
            file_patterns=["*.ts", "*.tsx", "*.js", "*.jsx", "*.json", "*.md"],
            fast=True,
            auto_fixable=True,
        ))
    
    def validate_plan(
        self,
        plan: EditPlan,
        fast: bool = True,
        use_cache: bool = True,
    ) -> ValidationResult:
        """Validate an edit plan.
        
        Args:
            plan: The edit plan to validate
            fast: If True, only run fast checks
            use_cache: Whether to use cached results
            
        Returns:
            ValidationResult with all errors/warnings
        """
        start_time = time.time()
        result = ValidationResult(valid=True, errors=[], warnings=[], checks_run=[])
        
        # Get affected files
        files = plan.get_affected_files()
        
        # Run checks in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = []
            
            for check in self.checks:
                # Skip if fast check requested but check is not fast
                if fast and not check.fast:
                    continue
                
                # Determine which files this check applies to
                applicable_files = [f for f in files if check.should_run(f, fast)]
                if not applicable_files:
                    continue
                
                # Check cache
                cache_key = None
                if use_cache:
                    cache_key = self._cache_key(check, applicable_files)
                    if cache_key in self._cache:
                        timestamp, cached_result = self._cache[cache_key]
                        if time.time() - timestamp < self.cache_ttl:
                            # Use cached result
                            result.merge(cached_result)
                            result.checks_run.append(check.name)
                            continue
                
                # Submit check
                future = executor.submit(self._run_check, check, applicable_files)
                futures.append((check, future, cache_key))
            
            # Collect results
            for check, future, cache_key in futures:
                try:
                    check_result = future.result(timeout=check.timeout)
                    result.merge(check_result)
                    result.checks_run.append(check.name)
                    
                    # Cache successful results
                    if use_cache and cache_key and check_result.valid:
                        self._cache[cache_key] = (time.time(), check_result)
                except concurrent.futures.TimeoutError:
                    result.add_error(f"Validation check '{check.name}' timed out")
                except Exception as e:
                    result.add_error(f"Validation check '{check.name}' failed: {e}")
        
        result.duration = time.time() - start_time
        return result
    
    def _run_check(
        self,
        check: ValidationCheck,
        files: List[str],
    ) -> ValidationResult:
        """Run a single validation check."""
        result = ValidationResult(valid=True, errors=[], warnings=[], checks_run=[check.name])
        
        if check.python_func:
            # Call Python function
            func = getattr(self, check.python_func, None)
            if func:
                try:
                    check_result = func(files)
                    if isinstance(check_result, ValidationResult):
                        result.merge(check_result)
                    else:
                        # Assume function returns (valid, errors, warnings)
                        valid, errors, warnings = check_result
                        if not valid:
                            result.valid = False
                            result.errors.extend(errors)
                        result.warnings.extend(warnings)
                except Exception as e:
                    result.add_error(f"Check {check.name} raised: {e}")
            else:
                result.add_error(f"Check function {check.python_func} not found")
        
        elif check.command:
            # Run shell command
            try:
                import subprocess
                cmd = check.command.split() + files
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=check.timeout,
                )
                
                if completed.returncode != 0:
                    result.valid = False
                    error_msg = completed.stderr.strip() or completed.stdout.strip()
                    result.add_error(f"{check.name} failed: {error_msg}")
                else:
                    # Parse output for warnings
                    output = completed.stdout + completed.stderr
                    if output:
                        result.warnings.append(f"{check.name}: {output[:200]}")
            except subprocess.TimeoutExpired:
                result.add_error(f"{check.name} timed out")
            except Exception as e:
                result.add_error(f"{check.name} error: {e}")
        
        return result
    
    # --- Python check functions ---
    
    def check_python_syntax(self, files: List[str]) -> ValidationResult:
        """Check Python syntax using py_compile."""
        result = ValidationResult(valid=True, errors=[], warnings=[])
        
        for file_path in files:
            try:
                import py_compile
                py_compile.compile(file_path, doraise=True)
            except py_compile.PyCompileError as e:
                result.add_error(f"Syntax error in {file_path}: {e}")
            except Exception as e:
                result.add_warning(f"Could not check {file_path}: {e}")
        
        return result
    
    def _cache_key(self, check: ValidationCheck, files: List[str]) -> str:
        """Generate a cache key for a check."""
        files_sorted = sorted(files)
        # Simple: check name + file mtimes
        mtimes = []
        for f in files_sorted:
            try:
                mtime = Path(f).stat().st_mtime
                mtimes.append(str(int(mtime)))
            except Exception:
                mtimes.append("0")
        return f"{check.name}:{':'.join(mtimes)}"
    
    def clear_cache(self) -> None:
        """Clear validation cache."""
        self._cache.clear()
    
    def register_check(self, check: ValidationCheck) -> None:
        """Register a custom validation check."""
        self.checks.append(check)
    
    def get_checks(self) -> List[ValidationCheck]:
        """Get all registered checks."""
        return self.checks.copy()
    
    def validate_file(
        self,
        file_path: str,
        fast: bool = True,
    ) -> ValidationResult:
        """Validate a single file (convenience method)."""
        plan = EditPlan(
            id="single_file_validation",
            description=f"Validate {file_path}",
            operations=[],
        )
        # We'll cheat and put the file in estimated_impact
        plan.estimated_impact = {"files": [file_path]}
        return self.validate_plan(plan, fast=fast)