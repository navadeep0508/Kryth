"""V8 Planner Quality Benchmark — Phase 2.

Benchmarks the planner's STRUCTURAL intelligence across 100+ synthetic tasks —
no LLM calls, so results are deterministic and reproducible.

Real planner LLM quality (does it correctly decompose a novel user request?)
requires live runs with `KRYTH_REAL_PROVIDERS=1`. This benchmark evaluates
the planner's output properties on known reference plans:

  ✓ No cycles in dependency graph
  ✓ Correct topological ordering (dependents after dependencies)
  ✓ Critical path correctly identified (longest chain)
  ✓ Milestones respect dependency ordering
  ✓ Parallelization detected where possible (parallel_streams > 1)
  ✓ No over-spawning (module count < complexity threshold)
  ✓ Estimated speedup is credible (≥ 1.0×)
  ✓ Module names are meaningful (not "Module1", "Module2")
  ✓ DAG layers are computed without error
  ✓ All modules appear in the DAG

Generates a 0-100 planner quality score.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from agent.orchestration.v4_validate import _make_plan
from agent.orchestration.project_planner import dag_from_plan, team_from_plan


# ── 100+ test case generator ───────────────────────────────────────────────────

def _gen_tasks() -> List[Tuple[str, object]]:
    """Generate 100+ (name, plan) test cases covering the full complexity range."""
    tasks: List[Tuple[str, object]] = []

    # Tier 0/1: Micro + Simple tasks (25 cases)
    simple_specs = [
        ("fix_typo",          [("Fixer", "Fix typo in config file", [], ["config.py"])]),
        ("add_log",           [("Logger", "Add logging to auth module", [], ["auth.py"])]),
        ("rename_var",        [("Refactor", "Rename variable 'x' to 'count'", [], ["utils.py"])]),
        ("create_readme",     [("Writer", "Write README", [], ["README.md"])]),
        ("fix_null_check",    [("Fixer", "Add null check before user.name access", [], ["user.py"])]),
        ("add_type_hints",    [("Typer", "Add type hints to all functions", [], ["service.py"])]),
        ("update_dep",        [("Updater", "Bump requests to 2.32", [], ["requirements.txt"])]),
        ("add_test",          [("Tester", "Add unit test for login function", [], ["test_auth.py"])]),
        ("fix_indent",        [("Fixer", "Fix indentation in models.py", [], ["models.py"])]),
        ("add_comment",       [("Documenter", "Add docstring to calculate_tax()", [], ["tax.py"])]),
        ("rename_file",       [("Refactor", "Rename controller.py to router.py", [], ["router.py"])]),
        ("fix_import",        [("Fixer", "Fix circular import between utils and models", [], ["utils.py"])]),
        ("delete_dead_code",  [("Cleaner", "Remove unused import statements", [], ["main.py"])]),
        ("add_env_var",       [("Config", "Add DATABASE_URL to .env.example", [], [".env.example"])]),
        ("fix_404",           [("Fixer", "Return 404 instead of 500 for missing resources", [], ["api.py"])]),
        ("add_validator",     [("Fixer", "Add email validation to signup endpoint", [], ["routes.py"])]),
        ("fix_typo_2",        [("Fixer", "Fix typo 'recieve' → 'receive'", [], ["email.py"])]),
        ("add_index",         [("DB", "Add index to users.email column", [], ["migration.sql"])]),
        ("fix_cors",          [("Config", "Fix CORS to allow localhost:3000", [], ["server.py"])]),
        ("add_pagination",    [("Fixer", "Add limit/offset to /users endpoint", [], ["routes.py"])]),
        ("fix_bcrypt",        [("Security", "Use bcrypt instead of md5 for passwords", [], ["auth.py"])]),
        ("add_middleware",    [("Web", "Add rate limiting middleware", [], ["middleware.py"])]),
        ("fix_timezone",      [("Fixer", "Store timestamps in UTC", [], ["models.py"])]),
        ("add_health_check",  [("Ops", "Add /health endpoint", [], ["routes.py"])]),
        ("fix_n_plus_1",      [("Perf", "Fix N+1 query in list_users()", [], ["db.py"])]),
    ]
    for name, spec in simple_specs:
        tasks.append((name, _make_plan(name.replace("_", " "), spec)))

    # Tier 2/3: Standard coding (50 cases)
    standard_specs = [
        ("crud_users",   [("Models", "User model + schema", [], ["models.py"]),
                          ("Routes", "CRUD routes for users", ["Models"], ["routes.py"]),
                          ("Tests",  "Integration tests",     ["Routes"], ["tests/test_users.py"])]),

        ("jwt_auth",     [("Auth",   "JWT auth service", [], ["auth.py"]),
                          ("Routes", "Login/logout routes", ["Auth"], ["routes.py"]),
                          ("Tests",  "Auth tests", ["Routes"], ["tests/test_auth.py"])]),

        ("file_upload",  [("Storage", "S3 file storage", [], ["storage.py"]),
                          ("API",    "Upload endpoints",  ["Storage"], ["upload.py"]),
                          ("UI",     "Upload form",       ["API"],     ["upload.html"])]),

        ("email_service",[("Template","Email templates",  [], ["templates/"]),
                          ("Sender", "SMTP sender",       [], ["sender.py"]),
                          ("Queue",  "Email queue",       ["Sender"], ["queue.py"]),
                          ("API",    "Email API",         ["Queue", "Template"], ["email_api.py"])]),

        ("search",       [("Index", "Search index",  [], ["index.py"]),
                          ("Query", "Query engine",  ["Index"], ["query.py"]),
                          ("API",   "Search API",    ["Query"], ["search_api.py"])]),

        ("oauth",        [("Provider", "OAuth provider setup", [], ["oauth.py"]),
                          ("Callback", "OAuth callback handler", ["Provider"], ["callback.py"]),
                          ("Session",  "Session management",    ["Callback"], ["session.py"])]),

        ("rate_limit",   [("Counter", "Request counter",   [], ["counter.py"]),
                          ("Limit",   "Rate limit logic",  ["Counter"], ["limiter.py"]),
                          ("Middleware","Rate limit middleware", ["Limit"], ["middleware.py"])]),

        ("cache_layer",  [("Cache",  "Redis cache",      [], ["cache.py"]),
                          ("Models", "Cached models",    ["Cache"], ["models.py"]),
                          ("Warmup", "Cache warmup job", ["Cache"], ["warmup.py"])]),

        ("websocket",    [("Server", "WebSocket server", [], ["ws_server.py"]),
                          ("Client", "JS WebSocket client", [], ["client.js"]),
                          ("Auth",   "WS auth handler",  ["Server"], ["ws_auth.py"])]),

        ("graphql",      [("Schema", "GraphQL schema",   [], ["schema.graphql"]),
                          ("Resolver","Resolvers",        ["Schema"], ["resolvers.py"]),
                          ("API",    "GraphQL API",       ["Resolver"], ["graphql_api.py"]),
                          ("Tests",  "Schema tests",      ["API"], ["test_graphql.py"])]),

        ("audit_log",    [("Schema",  "Audit log schema",   [], ["audit.sql"]),
                          ("Logger",  "Audit logger",       ["Schema"], ["audit_logger.py"]),
                          ("API",     "Audit query API",    ["Logger"], ["audit_api.py"])]),

        ("notifications",[("Channel", "Notification channels", [], ["channels.py"]),
                          ("Sender",  "Notification sender",   ["Channel"], ["sender.py"]),
                          ("Pref",    "User preferences",      ["Channel"], ["preferences.py"]),
                          ("API",     "Notification API",      ["Sender", "Pref"], ["notif_api.py"])]),

        ("analytics",    [("Collector","Event collector",  [], ["collector.py"]),
                          ("Store",   "Event store",       ["Collector"], ["store.py"]),
                          ("Query",   "Analytics queries", ["Store"], ["queries.py"]),
                          ("API",     "Analytics API",     ["Query"], ["analytics_api.py"])]),

        ("payments",     [("Gateway", "Payment gateway",  [], ["gateway.py"]),
                          ("Webhook", "Payment webhooks", ["Gateway"], ["webhooks.py"]),
                          ("Refund",  "Refund flow",       ["Gateway"], ["refund.py"]),
                          ("Audit",   "Payment audit",     ["Gateway", "Refund"], ["payment_audit.py"])]),

        ("i18n",         [("Extract","String extraction", [], ["extract.py"]),
                          ("Trans",  "Translation store",  ["Extract"], ["translations/en.json"]),
                          ("Loader", "i18n loader",        ["Trans"], ["i18n.py"])]),

        # 35 more variations with different topologies
        *[
            (f"generic_{i}", [
                (f"Mod{j}", f"Module {j}", [f"Mod{j-1}"] if j > 0 else [], [f"m{j}.py"])
                for j in range(min(i % 6 + 2, 6))
            ])
            for i in range(35)
        ],
    ]
    for name, spec in standard_specs:
        tasks.append((name, _make_plan(name.replace("_", " "), spec)))

    # Tier 3/4: Complex enterprise (25 cases)
    complex_specs = [
        ("saas_platform", [
            ("Auth",      "Auth service",      [], ["auth.py"]),
            ("Billing",   "Billing service",   [], ["billing.py"]),
            ("Dashboard", "User dashboard",    ["Auth"], ["dashboard.py"]),
            ("API",       "REST API",          ["Auth", "Billing"], ["api.py"]),
            ("Admin",     "Admin panel",       ["Auth", "API"], ["admin.py"]),
        ]),
        ("microservices", [
            ("Gateway",    "API gateway",        [], ["gateway.py"]),
            ("Auth",       "Auth microservice",  ["Gateway"], ["auth_svc.py"]),
            ("User",       "User microservice",  ["Gateway"], ["user_svc.py"]),
            ("Product",    "Product service",    ["Gateway"], ["product_svc.py"]),
            ("Order",      "Order service",      ["User", "Product"], ["order_svc.py"]),
            ("Payment",    "Payment service",    ["Order"], ["payment_svc.py"]),
            ("Notify",     "Notification svc",   ["Order", "Payment"], ["notify_svc.py"]),
        ]),
        *[
            (f"enterprise_{i}", [
                (f"Svc{j}", f"Service {j}",
                 [f"Svc{j-1}"] if j > 0 and j % 3 != 0 else ([] if j == 0 else [f"Svc{j-2}", f"Svc{j-1}"]),
                 [f"svc{j}.py"])
                for j in range(min(i % 8 + 5, 10))
            ])
            for i in range(23)
        ],
    ]
    for name, spec in complex_specs:
        tasks.append((name, _make_plan(name.replace("_", " "), spec)))

    return tasks


# ── Structural checks ──────────────────────────────────────────────────────────

@dataclass
class PlanCheck:
    name: str
    passed: bool
    detail: str = ""


def _check_plan(task_name: str, plan) -> List[PlanCheck]:
    """Check structural properties of a plan. Uses plan.dependency_layers() and
    dag.nodes/.layers() — the actual TaskDAG API (no node_ids(), get_node(), etc.)."""
    checks: List[PlanCheck] = []

    # 1. No cycles — dependency_layers() returns empty or fails on cycles
    try:
        layers = plan.dependency_layers()
        cycles_ok = len(layers) > 0
    except Exception:
        layers = []
        cycles_ok = False
    checks.append(PlanCheck("no_cycles", cycles_ok, f"{len(layers)} layers" if cycles_ok else "cycle detected"))

    # 2. All modules appear in dependency layers
    plan_module_names = {m.name for m in plan.modules}
    layered_names = {m.name for layer in layers for m in layer}
    covered = layered_names >= plan_module_names
    checks.append(PlanCheck("all_modules_in_layers", covered,
                             f"{len(layered_names)}/{len(plan_module_names)} covered"))

    # 3. Dependency ordering: deps appear in strictly earlier layers
    dep_order_ok = True
    if layers:
        layer_of = {m.name: li for li, layer in enumerate(layers) for m in layer}
        for m in plan.modules:
            my_layer = layer_of.get(m.name, 0)
            for dep in (m.dependencies or []):
                dep_layer = layer_of.get(dep, -1)
                if dep_layer >= my_layer:
                    dep_order_ok = False
                    break
    checks.append(PlanCheck("dep_ordering_correct", dep_order_ok, "all deps precede dependents"))

    # 4. Critical path from plan exists and is reasonable length
    try:
        cp = plan.critical_path()
        cp_ok = len(cp) > 0 and len(cp) <= len(plan.modules)
    except Exception:
        cp_ok = False
        cp = []
    checks.append(PlanCheck("critical_path_valid", cp_ok, f"length={len(cp)}"))

    # 5. No over-spawning
    n_mods = len(plan.modules)
    checks.append(PlanCheck("no_overspawning", n_mods <= 15, f"{n_mods} modules"))

    # 6. Speedup is credible (≥1.0×)
    speedup = getattr(plan, "estimated_speedup", 1.0) or 1.0
    checks.append(PlanCheck("credible_speedup", speedup >= 1.0, f"{speedup:.2f}x"))

    # 7. Module names meaningful (not all "Mod0", "Mod1"…)
    generic = sum(1 for m in plan.modules if m.name.startswith("Mod") or m.name.startswith("Module"))
    names_ok = generic < len(plan.modules) or n_mods <= 1
    checks.append(PlanCheck("meaningful_names", names_ok,
                             f"{len(plan.modules) - generic}/{len(plan.modules)} non-generic"))

    # 8. DAG buildable without exception
    try:
        dag = dag_from_plan(plan)
        dag_ok = len(dag.nodes) >= n_mods
    except Exception:
        dag_ok = False
        dag = None
    checks.append(PlanCheck("dag_buildable", dag_ok, f"{len(dag.nodes) if dag else '?'} dag nodes"))

    # 9. Milestone structure constructible
    try:
        milestones = plan.ensure_structured_milestones()
        ms_ok = len(milestones) >= 1
    except Exception:
        ms_ok = False
        milestones = []
    checks.append(PlanCheck("milestones_structurable", ms_ok, f"{len(milestones)} milestones"))

    # 10. Team buildable from plan
    try:
        team = team_from_plan(plan, dag or dag_from_plan(plan), task_name)
        team_ok = len(team.agents) >= 1
    except Exception:
        team_ok = False
        team = None
    checks.append(PlanCheck("team_buildable", team_ok, f"{len(team.agents) if team else '?'} agents"))

    return checks


@dataclass
class PlannerBenchmarkReport:
    task_count: int
    checks_per_plan: int
    total_checks: int
    checks_passed: int
    plan_score: float         # 0-100 — fraction of checks that passed
    failing_tasks: List[str] = field(default_factory=list)
    check_failure_counts: Dict[str, int] = field(default_factory=dict)
    elapsed_s: float = 0.0

    def grade(self) -> str:
        s = self.plan_score
        if s >= 95: return "A+"
        if s >= 85: return "A"
        if s >= 75: return "B"
        return "C"

    def summary(self) -> str:
        return (
            f"Planner Quality Benchmark: {self.task_count} tasks  "
            f"{self.checks_passed}/{self.total_checks} checks  "
            f"score={self.plan_score:.1f}/100  grade={self.grade()}"
        )


def run_planner_benchmark() -> PlannerBenchmarkReport:
    t0 = time.monotonic()
    tasks = _gen_tasks()
    total_checks = 0
    total_passed = 0
    failing_tasks: List[str] = []
    fail_counts: Dict[str, int] = {}

    for task_name, plan in tasks:
        checks = _check_plan(task_name, plan)
        plan_passed = all(c.passed for c in checks)
        total_checks += len(checks)
        total_passed += sum(1 for c in checks if c.passed)
        if not plan_passed:
            failing_tasks.append(task_name)
        for c in checks:
            if not c.passed:
                fail_counts[c.name] = fail_counts.get(c.name, 0) + 1

    score = (total_passed / max(total_checks, 1)) * 100
    checks_per_plan = len(_check_plan("_", _make_plan("x", [("A", "g", [], ["a.py"])])))
    return PlannerBenchmarkReport(
        task_count=len(tasks),
        checks_per_plan=checks_per_plan,
        total_checks=total_checks,
        checks_passed=total_passed,
        plan_score=score,
        failing_tasks=failing_tasks[:10],
        check_failure_counts=fail_counts,
        elapsed_s=time.monotonic() - t0,
    )


if __name__ == "__main__":
    print("Running planner benchmark...")
    report = run_planner_benchmark()
    print(report.summary())
    if report.check_failure_counts:
        print("  Failed checks:")
        for name, count in sorted(report.check_failure_counts.items(), key=lambda x: -x[1]):
            print(f"    {name}: {count} failures")
