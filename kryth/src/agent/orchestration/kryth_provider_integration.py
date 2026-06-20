"""KRYTH Provider Integration — discover providers from KRYTH config.

V8 certification and benchmarks import this instead of checking raw
provider-specific env vars.  All credential resolution goes through
KRYTH's existing model_config stack (loader → providers → router).

Public API
----------
get_available_kryth_providers() -> List[KrythProviderInfo]
    Enumerate every provider KRYTH knows about and whether it has credentials.

require_live_providers(verbose=True) -> List[KrythProviderInfo]
    Assert at least one provider is usable; sys.exit with a KRYTH-aware
    error message on failure — never emits raw env-var error strings.

describe_provider_status() -> str
    Human-readable table of provider availability.

get_live_routing_context() -> LiveRoutingContext
    The exact routing context (planner model, worker model, …) that KRYTH
    would use for a real mission — used by certification to confirm that
    the benchmark exercises real production paths.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import List


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class KrythProviderInfo:
    """Availability record for a single LLM provider."""
    name: str               # e.g. "openai", "anthropic", "groq"
    enabled: bool           # True when credentials are present or not required
    credential_source: str  # "kryth_config" | "env_var" | "local" | "missing"
    models: List[str] = field(default_factory=list)   # role:model assignments
    base_url: str = ""
    cost_tier: str = ""     # "local" | "low-cost" | "paid"
    notes: str = ""


@dataclass
class LiveRoutingContext:
    """Routing context KRYTH will use during a live run."""
    planner_model: str
    worker_model: str
    vision_model: str
    summary_model: str
    fallback_chain: List[str]   # provider names in declared fallback order


# ---------------------------------------------------------------------------
# Provider discovery
# ---------------------------------------------------------------------------

def get_available_kryth_providers() -> List[KrythProviderInfo]:
    """Discover available providers through KRYTH's existing config system.

    Reads, in priority order:
      1. Providers explicitly registered in ~/.kryth/config.yaml
      2. Credentials resolvable from env vars recognised by KRYTH's provider
         registry  (PROVIDER_KEY_ENV in model_config.providers)
      3. Unified-mode api_key from KRYTH config

    Local providers (Ollama, LM Studio) are always listed as enabled because
    they need no API key; they may still be offline at call time.
    """
    from agent.model_config.loader import get_config
    from agent.model_config.providers import PROVIDER_KEY_ENV, PROVIDER_DEFAULTS

    try:
        cfg = get_config()
    except Exception:
        cfg = None

    infos: List[KrythProviderInfo] = []
    seen: set[str] = set()

    # ── 1. Providers explicitly registered in KRYTH config ───────────────────
    if cfg is not None:
        for pname, pcfg in (cfg.providers or {}).items():
            seen.add(pname)
            cfg_key = (getattr(pcfg, "api_key", "") or "").strip()
            has_cfg_key = bool(cfg_key) and cfg_key not in ("not-configured", "not-set")

            # KRYTH's loader also reads env vars — check them too
            env_var = PROVIDER_KEY_ENV.get(pname, "")
            has_env_key = False
            if not has_cfg_key and env_var:
                has_env_key = bool(os.environ.get(env_var, "").strip())

            enabled = has_cfg_key or has_env_key
            source = "kryth_config" if enabled else "missing"

            infos.append(KrythProviderInfo(
                name=pname,
                enabled=enabled,
                credential_source=source,
                models=_models_for_provider(cfg, pname),
                base_url=(getattr(pcfg, "base_url", "") or "")
                         or PROVIDER_DEFAULTS.get(pname, {}).get("base_url", ""),
                cost_tier=_cost_tier(pname),
            ))

    # ── 2. Providers detectable from env vars not yet in explicit config ──────
    for pname, env_var in sorted(PROVIDER_KEY_ENV.items()):
        if pname in seen:
            continue
        seen.add(pname)

        if not env_var:
            # Local provider — no API key required
            base_url = PROVIDER_DEFAULTS.get(pname, {}).get("base_url", "")
            infos.append(KrythProviderInfo(
                name=pname,
                enabled=True,
                credential_source="local",
                models=_models_for_provider(cfg, pname) if cfg else [],
                base_url=base_url,
                cost_tier="local",
                notes="local endpoint, no API key required",
            ))
        else:
            key = os.environ.get(env_var, "").strip()
            if key and key not in ("not-configured", "not-set"):
                infos.append(KrythProviderInfo(
                    name=pname,
                    enabled=True,
                    credential_source="kryth_config",
                    models=_models_for_provider(cfg, pname) if cfg else [],
                    base_url=PROVIDER_DEFAULTS.get(pname, {}).get("base_url", ""),
                    cost_tier=_cost_tier(pname),
                ))

    # ── 3. Unified mode: single provider + key ────────────────────────────────
    if cfg is not None and cfg.mode == "unified" and cfg.api_key:
        key = (cfg.api_key or "").strip()
        if key and key not in ("not-configured", "not-set"):
            pname = cfg.provider or _infer_provider_from_url(cfg.base_url)
            if pname not in seen:
                seen.add(pname)
                infos.append(KrythProviderInfo(
                    name=pname,
                    enabled=True,
                    credential_source="kryth_config",
                    models=([f"main:{cfg.model}"] if cfg.model else []),
                    base_url=cfg.base_url or "",
                    cost_tier=_cost_tier(pname),
                    notes="unified mode",
                ))

    return infos


def require_live_providers(verbose: bool = True) -> List[KrythProviderInfo]:
    """Return enabled (non-local) providers or exit with a KRYTH-aware error.

    Call this before any live benchmark/certification run.  On failure it
    prints a structured error that references KRYTH config, not raw env vars,
    then calls ``sys.exit(1)``.
    """
    providers = get_available_kryth_providers()

    # Prefer providers that have real API keys
    api_providers = [p for p in providers if p.enabled and p.credential_source != "local"]
    # Fall back to local providers if that's all that's configured
    local_providers = [p for p in providers if p.enabled and p.credential_source == "local"]

    available = api_providers or local_providers

    if not available:
        if verbose:
            _print_no_providers_error(providers)
        sys.exit(1)

    return available


# ---------------------------------------------------------------------------
# Status reporting
# ---------------------------------------------------------------------------

def describe_provider_status() -> str:
    """Return a human-readable table of KRYTH provider availability."""
    providers = get_available_kryth_providers()
    active = [p for p in providers if p.enabled]
    inactive = [p for p in providers if not p.enabled]

    lines = ["KRYTH Provider Status:"]

    if active:
        lines.append(f"  Active ({len(active)}):")
        for p in active:
            model_str = ", ".join(p.models[:2]) if p.models else "default routing"
            tag = f"[{p.credential_source}]"
            lines.append(f"    ✓  {p.name:<14} {tag:<18} {model_str}")

    if inactive:
        lines.append(f"  No credentials ({len(inactive)}):")
        for p in inactive:
            lines.append(f"    ✗  {p.name:<14} configure in ~/.kryth/config.yaml")

    if not providers:
        lines.append("  ✗  No providers discovered")
        lines.append("     Add a provider to ~/.kryth/config.yaml to enable live runs")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Live routing context
# ---------------------------------------------------------------------------

def get_live_routing_context() -> LiveRoutingContext:
    """Return the routing context KRYTH will use for a live run.

    Reads from the same router as production — certification therefore
    validates actual production routing, not a benchmark-specific override.
    """
    from agent.model_config.router import get_model_name
    from agent.model_config.loader import get_config

    roles: dict[str, str] = {}
    for role in ("main", "planner", "summary", "vision", "extraction", "reasoning"):
        try:
            roles[role] = get_model_name(role)
        except Exception:
            roles[role] = "<not configured>"

    try:
        cfg = get_config()
        fallback_chain = list(cfg.providers.keys()) or [cfg.provider or "openai"]
        # Append fallback providers from config.fallbacks keys
        for fb_provider in cfg.fallbacks:
            if fb_provider not in fallback_chain:
                fallback_chain.append(fb_provider)
    except Exception:
        fallback_chain = ["openai"]

    return LiveRoutingContext(
        planner_model=roles.get("planner", "<not configured>"),
        worker_model=roles.get("main", "<not configured>"),
        vision_model=roles.get("vision", "<not configured>"),
        summary_model=roles.get("summary", "<not configured>"),
        fallback_chain=fallback_chain,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _models_for_provider(cfg, provider_name: str) -> List[str]:
    """Collect role:model strings assigned to this provider from KRYTH config."""
    if cfg is None:
        return []
    models: List[str] = []
    for role, spec in (getattr(cfg, "models", {}) or {}).items():
        if getattr(spec, "provider", "") == provider_name and getattr(spec, "model", ""):
            models.append(f"{role}:{spec.model}")
    if getattr(cfg, "mode", "") == "unified":
        pname = getattr(cfg, "provider", "") or _infer_provider_from_url(
            getattr(cfg, "base_url", "")
        )
        if pname == provider_name and getattr(cfg, "model", ""):
            models.append(f"main:{cfg.model}")
    return models


def _infer_provider_from_url(base_url: str) -> str:
    url = (base_url or "").lower()
    if "openrouter" in url:    return "openrouter"
    if "anthropic" in url:     return "anthropic"
    if "generativelanguage" in url or "google" in url: return "google"
    if "nvidia" in url or "nvapi" in url: return "nvidia"
    if "groq" in url:          return "groq"
    if "localhost" in url or "127.0.0.1" in url: return "ollama"
    return "openai"


def _cost_tier(provider_name: str) -> str:
    local   = {"ollama", "lmstudio"}
    budget  = {"groq", "openrouter", "nvidia"}
    if provider_name in local:  return "local"
    if provider_name in budget: return "low-cost"
    return "paid"


def _print_no_providers_error(providers: List[KrythProviderInfo]) -> None:
    """Print a structured, KRYTH-aware error — never raw env-var error strings."""
    from agent.model_config.providers import PROVIDER_KEY_ENV

    print()
    print("  ✗  No active providers found in KRYTH config")
    print()
    print("  KRYTH has no LLM provider with valid credentials.")
    print()
    print("  Recommended fixes:")
    print("    1. Configure a provider in KRYTH settings  (~/.kryth/config.yaml)")
    print("    2. Enable routing and verify model selection in KRYTH")
    print("    3. Or use a local provider (Ollama, LM Studio) — no API key needed")
    print("    4. Then retry:  python -m agent.orchestration.v8_scorecard --live")
    print()
    print("  KRYTH resolves provider credentials from its config system.")
    print("  The following env vars are part of KRYTH's provider resolution:")
    print()
    for pname, env_var in sorted(PROVIDER_KEY_ENV.items()):
        if env_var:
            val = os.environ.get(env_var, "").strip()
            status = "✓ set" if val and val not in ("not-configured", "not-set") else "✗ missing"
            print(f"    {pname:<14}  {env_var:<28}  [{status}]")
    print()
    print("  Example ~/.kryth/config.yaml (unified mode):")
    print("    mode: unified")
    print("    provider: openai")
    print("    model: gpt-4o-mini")
    print("    api_key: ${OPENAI_API_KEY}")
    print()
    print("  Example ~/.kryth/config.yaml (specialized mode):")
    print("    mode: specialized")
    print("    providers:")
    print("      anthropic:")
    print("        api_key: ${ANTHROPIC_API_KEY}")
    print("      openrouter:")
    print("        api_key: ${OPENROUTER_API_KEY}")
    print("    models:")
    print("      planner:  {provider: anthropic, model: claude-sonnet-4-6}")
    print("      main:     {provider: openrouter, model: gpt-4o-mini}")
