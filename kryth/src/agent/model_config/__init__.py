"""KRYTH Model Configuration System.

Public API:

    from agent.model_config import get_llm, get_model_name, get_client
    from agent.model_config import get_config, reload_config
    from agent.model_config import validate_and_report

Usage:

    # Get (client, model_name) for a role
    client, model = get_llm("planner")
    client, model = get_llm("vision")
    client, model = get_llm()          # defaults to "main"

    # Just the model name (for passing to client.chat.completions.create)
    model = get_model_name("summary")

    # Just the client
    client = get_client("extraction")

    # Reload after config change
    from agent.model_config import reload_config
    reload_config()

    # Startup health check (opt-in)
    from agent.model_config import validate_and_report
    validate_and_report()

Configuration:
    Create ~/.kryth/config.yaml for persistent settings.
    See agent/model_config/schema.py for the full schema.
    Existing env vars (KRYTH_MAIN_MODEL etc.) remain supported.
"""

from agent.model_config.loader import get_config, reload_config, load_config
from agent.model_config.router import (
    get_llm,
    get_model_name,
    get_client,
    pick_model_for_task,
    invalidate_cache,
)
from agent.model_config.validator import validate_and_report, quick_check
from agent.model_config.schema import KrythConfig, ProviderConfig, ModelSpec, ROLES

__all__ = [
    # Config access
    "get_config",
    "reload_config",
    "load_config",
    # Role-based routing
    "get_llm",
    "get_model_name",
    "get_client",
    "pick_model_for_task",
    "invalidate_cache",
    # Validation
    "validate_and_report",
    "quick_check",
    # Schema
    "KrythConfig",
    "ProviderConfig",
    "ModelSpec",
    "ROLES",
]
