"""Configuration loader — merges all config sources into a KrythConfig.

Priority (highest wins):
  5. CLI arguments (applied externally before load)
  4. Environment variables (KRYTH_MAIN_MODEL, KRYTH_BASE_URL, etc.)
  3. ~/.kryth/config.yaml or ~/.kryth/config.json
  2. <project>/.kryth/config.yaml or .kryth/config.json
  1. Built-in defaults

Backward compatibility:
  If no YAML exists, existing env vars are synthesized into a unified config.
  Existing ~/.ai-coder/config.json is read as a fallback.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from agent.env import getenv
from agent.model_config.schema import (
    KrythConfig, ModelSpec, ProviderConfig,
    DEFAULT_VISION_MODEL, DEFAULT_NVIDIA_BASE_URL,
    ROLES,
)


# ---------------------------------------------------------------------------
# Singleton + reload
# ---------------------------------------------------------------------------

_config: KrythConfig | None = None
_config_lock = threading.Lock()


def get_config() -> KrythConfig:
    """Return the cached config, loading it on first call."""
    global _config
    if _config is None:
        with _config_lock:
            if _config is None:
                _config = load_config()
    return _config


def reload_config() -> KrythConfig:
    """Force-reload config from all sources (called after /config changes)."""
    global _config
    with _config_lock:
        _config = load_config()
    return _config


# ---------------------------------------------------------------------------
# YAML loading (optional dependency)
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # yaml not installed — try JSON fallback
        json_path = path.with_suffix(".json")
        if json_path.exists():
            return _load_json(json_path)
        return {}
    except Exception:
        return {}


def _load_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Config file discovery
# ---------------------------------------------------------------------------

def _find_config_files() -> list[Path]:
    """Return config file paths in ascending priority order."""
    candidates = []

    # Legacy ~/.ai-coder/config.json (existing KRYTH config)
    legacy = Path.home() / ".ai-coder" / "config.json"
    if legacy.exists():
        candidates.append(legacy)

    # ~/.kryth/config.yaml (new primary user config)
    user_dir = Path.home() / ".kryth"
    for name in ("config.yaml", "config.yml", "config.json"):
        p = user_dir / name
        if p.exists():
            candidates.append(p)
            break

    # <cwd>/.kryth/config.yaml (project-level config)
    cwd_dir = Path.cwd() / ".kryth"
    for name in ("config.yaml", "config.yml", "config.json"):
        p = cwd_dir / name
        if p.exists():
            candidates.append(p)
            break

    return candidates


# ---------------------------------------------------------------------------
# Layer: legacy config.json (existing format)
# ---------------------------------------------------------------------------

def _parse_legacy_json(data: dict) -> dict:
    """Convert ~/.ai-coder/config.json to a partial KrythConfig dict."""
    out: dict[str, Any] = {"mode": "unified"}
    if data.get("model"):
        out["model"] = data["model"]
    if data.get("base_url"):
        out["base_url"] = data["base_url"]
    if data.get("api_key"):
        out["api_key"] = data["api_key"]
    # Planner / summarizer from legacy keys
    if data.get("planner_model"):
        out.setdefault("models", {})["planner"] = {"provider": "openai", "model": data["planner_model"]}
    return out


# ---------------------------------------------------------------------------
# Layer: environment variables (backward compat)
# ---------------------------------------------------------------------------

def _env_overrides() -> dict[str, Any]:
    """Synthesize env-var overrides into a partial config dict."""
    out: dict[str, Any] = {}

    main_model = getenv("KRYTH_MAIN_MODEL")
    planner_model = getenv("KRYTH_PLANNER_MODEL")
    summarizer_model = getenv("KRYTH_SUMMARIZER_MODEL")
    base_url = getenv("KRYTH_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "").strip()

    if main_model:
        out["model"] = main_model
    if base_url:
        out["base_url"] = base_url
    if api_key and api_key not in ("not-configured", "not-set"):
        out["api_key"] = api_key

    models: dict = {}
    if planner_model:
        models["planner"] = {"provider": "openai", "model": planner_model}
    if summarizer_model:
        models["summary"] = {"provider": "openai", "model": summarizer_model}

    # Vision: if NVIDIA key present + NVIDIA base_url or default
    if nvidia_key:
        vision_model = getenv("KRYTH_VISION_MODEL", DEFAULT_VISION_MODEL)
        nvidia_url = getenv("KRYTH_NVIDIA_BASE_URL", DEFAULT_NVIDIA_BASE_URL)
        models["vision"] = {"provider": "nvidia", "model": vision_model}
        out.setdefault("providers", {})["nvidia"] = {
            "api_key": nvidia_key,
            "base_url": nvidia_url,
        }

    # Additional role env vars
    for role, env_var in [
        ("extraction", "KRYTH_EXTRACTION_MODEL"),
        ("reasoning",  "KRYTH_REASONING_MODEL"),
        ("vision",     "KRYTH_VISION_MODEL"),
    ]:
        val = getenv(env_var)
        if val:
            models[role] = {"provider": "openai", "model": val}

    if models:
        out["models"] = models

    return out


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], val)
        elif val not in (None, "", [], {}):
            result[key] = val
    return result


# ---------------------------------------------------------------------------
# Build KrythConfig from raw dict
# ---------------------------------------------------------------------------

def _build_config(raw: dict) -> KrythConfig:
    """Convert a merged raw dict into a validated KrythConfig."""
    _PROVIDER_FIELDS = {"api_key", "base_url", "timeout", "max_retries", "headers", "proxy"}
    _MODEL_FIELDS = {"provider", "model", "fallbacks"}

    # Normalize providers
    providers: dict[str, ProviderConfig] = {}
    for name, pcfg in (raw.get("providers") or {}).items():
        if isinstance(pcfg, dict):
            try:
                providers[name] = ProviderConfig(**{k: v for k, v in pcfg.items()
                                                    if k in _PROVIDER_FIELDS})
            except Exception:
                pass

    # Normalize models
    models: dict[str, ModelSpec] = {}
    for role, mcfg in (raw.get("models") or {}).items():
        if isinstance(mcfg, dict):
            try:
                models[role] = ModelSpec(**{k: v for k, v in mcfg.items()
                                            if k in _MODEL_FIELDS})
            except Exception:
                pass

    # Auto-promote to specialized if per-role models are defined
    mode = raw.get("mode", "unified")
    if models and mode == "unified":
        mode = "specialized"

    return KrythConfig(
        mode=mode,
        providers=providers,
        models=models,
        provider=raw.get("provider") or "",
        model=raw.get("model") or "",
        base_url=raw.get("base_url") or "",
        api_key=raw.get("api_key") or "",
        routing=raw.get("routing") or {},
        fallbacks=raw.get("fallbacks") or {},
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def load_config() -> KrythConfig:
    """Load and merge all configuration sources into a KrythConfig."""
    raw: dict = {}

    # Load each config file in ascending priority
    for path in _find_config_files():
        if path.suffix == ".json":
            file_data = _load_json(path)
            # Detect legacy format (has "model" but not "mode")
            if "mode" not in file_data and "provider" not in file_data:
                file_data = _parse_legacy_json(file_data)
        else:
            file_data = _load_yaml(path)

        if file_data:
            raw = _deep_merge(raw, file_data)

    # Apply env var overrides (highest priority after CLI)
    env = _env_overrides()
    if env:
        raw = _deep_merge(raw, env)

    return _build_config(raw)


# ---------------------------------------------------------------------------
# Config file writing (for ~/.kryth/config.yaml)
# ---------------------------------------------------------------------------

def save_config(cfg: KrythConfig, path: Path | None = None) -> None:
    """Write config to ~/.kryth/config.yaml."""
    if path is None:
        path = Path.home() / ".kryth" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import yaml
        data: dict = {"mode": cfg.mode}
        if cfg.mode == "unified":
            if cfg.provider:
                data["provider"] = cfg.provider
            if cfg.model:
                data["model"] = cfg.model
            if cfg.base_url:
                data["base_url"] = cfg.base_url
        else:
            if cfg.providers:
                data["providers"] = {
                    name: {k: v for k, v in _provider_to_dict(pc).items() if v}
                    for name, pc in cfg.providers.items()
                }
            if cfg.models:
                data["models"] = {
                    role: _modelspec_to_dict(ms)
                    for role, ms in cfg.models.items()
                }
            if cfg.routing:
                data["routing"] = dict(cfg.routing)

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        path.chmod(0o600)
    except ImportError:
        # YAML not available — write JSON instead
        json_path = path.with_suffix(".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"mode": cfg.mode}, f, indent=2)
        json_path.chmod(0o600)


def _provider_to_dict(pc: ProviderConfig) -> dict:
    if hasattr(pc, "model_dump"):
        return pc.model_dump()
    import dataclasses
    return dataclasses.asdict(pc)


def _modelspec_to_dict(ms: ModelSpec) -> dict:
    if hasattr(ms, "model_dump"):
        return ms.model_dump()
    import dataclasses
    return dataclasses.asdict(ms)
