"""Tools module for the Kryth agent."""

from typing import TYPE_CHECKING

from tools.base import Tool, ToolKind, ToolResult, ToolInvocation, ToolConfirmation

if TYPE_CHECKING:
    from tools.registry import ToolRegistry, create_default_registry

__all__ = [
    'Tool',
    'ToolKind',
    'ToolResult',
    'ToolInvocation',
    'ToolConfirmation',
    'ToolRegistry',
    'create_default_registry',
]


def __getattr__(name: str):
    if name in {"ToolRegistry", "create_default_registry"}:
        from tools import registry

        return getattr(registry, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
