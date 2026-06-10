"""Dynamic tool registry — tools register themselves and can be looked up by name.

The registry provides:
- ``register`` / ``register_tool`` — add tool implementations
- ``get`` / ``list_tools`` — look up tools by name
- ``call`` — invoke a tool by name with arguments
- Tool grouping and dependency tracking
- Backward compatibility with the existing TOOLS dict

Usage:
    from agent.tools.registry import registry

    # Register a tool
    registry.register("my_tool", my_handler)

    # Call a tool
    result = registry.call("my_tool", arg1="value")

    # Get all registered tool names
    names = registry.list_tools()
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class ToolRegistration:
    """Metadata about a registered tool."""

    def __init__(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        category: str = "general",
        params_schema: dict | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self.name = name
        self.handler = handler
        self.description = description or (handler.__doc__ or "").strip().split("\n")[0]
        self.category = category
        self.params_schema = params_schema or self._infer_schema(handler)
        self.tags = tags or []

    def _infer_schema(self, handler: Callable) -> dict:
        """Infer a parameter schema from the function signature."""
        sig = inspect.signature(handler)
        properties = {}
        required = []
        for name, param in sig.parameters.items():
            if name in ("self", "cls", "kwargs", "args"):
                continue
            param_type = "string"
            if param.annotation != inspect.Parameter.empty:
                ann = param.annotation
                if ann in (int, float):
                    param_type = "number"
                elif ann is bool:
                    param_type = "boolean"
                elif ann in (list, tuple):
                    param_type = "array"
                elif ann is dict:
                    param_type = "object"
            properties[name] = {"type": param_type}
            if param.default == inspect.Parameter.empty:
                required.append(name)
        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def call(self, **kwargs) -> Any:
        return self.handler(**kwargs)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "params_schema": self.params_schema,
            "tags": self.tags,
        }


class ToolRegistry:
    """Dynamic registry of available tools.

    Maintains a map of tool name -> ToolRegistration, with support for
    tool categories, tags, and parameter schema inference.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolRegistration] = {}
        self._categories: dict[str, list[str]] = {}

    def register(
        self,
        name: str,
        handler: Callable,
        *,
        description: str = "",
        category: str = "general",
        tags: list[str] | None = None,
    ) -> None:
        """Register a tool handler.

        Args:
            name: Unique tool name.
            handler: Callable that implements the tool.
            description: Short description of what the tool does.
            category: Tool category (filesystem, browser, shell, etc.).
            tags: Optional list of tags for search/filtering.
        """
        existing = self._tools.get(name)
        if existing and existing.handler is not handler:
            logger.warning("Overwriting existing tool registration: %s", name)

        reg = ToolRegistration(
            name=name, handler=handler,
            description=description, category=category,
            tags=tags,
        )
        self._tools[name] = reg

        if category not in self._categories:
            self._categories[category] = []
        if name not in self._categories[category]:
            self._categories[category].append(name)

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry."""
        reg = self._tools.pop(name, None)
        if reg:
            cat_list = self._categories.get(reg.category, [])
            if name in cat_list:
                cat_list.remove(name)

    def get(self, name: str) -> ToolRegistration | None:
        """Get a tool registration by name."""
        return self._tools.get(name)

    def call(self, name: str, **kwargs) -> Any:
        """Call a tool by name with keyword arguments.

        Raises KeyError if the tool is not registered.
        """
        reg = self._tools.get(name)
        if reg is None:
            raise KeyError(f"Unknown tool: {name}. Available: {', '.join(self.list_tools())}")
        try:
            return reg.call(**kwargs)
        except Exception as e:
            logger.error("Tool '%s' failed: %s", name, e)
            raise

    def list_tools(self, category: str = "") -> list[str]:
        """List registered tool names, optionally filtered by category."""
        if category:
            return self._categories.get(category, [])
        return sorted(self._tools.keys())

    def list_categories(self) -> list[str]:
        return sorted(self._categories.keys())

    def get_category(self, category: str) -> list[ToolRegistration]:
        """Get all tool registrations in a category."""
        return [self._tools[name] for name in self._categories.get(category, []) if name in self._tools]

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def search(self, query: str) -> list[ToolRegistration]:
        """Search tools by name, description, or tags."""
        q = query.lower()
        results = []
        for reg in self._tools.values():
            if q in reg.name.lower() or q in reg.description.lower():
                results.append(reg)
                continue
            for tag in reg.tags:
                if q in tag.lower():
                    results.append(reg)
                    break
        return results

    def to_dict(self) -> dict:
        return {
            "tools": {name: reg.to_dict() for name, reg in self._tools.items()},
            "categories": dict(self._categories),
        }

    def register_tool_object(self, tool_obj: Any) -> None:
        """Register a tool-like object that has name, description, and __call__."""
        name = getattr(tool_obj, "name", tool_obj.__class__.__name__)
        description = getattr(tool_obj, "description", "")
        category = getattr(tool_obj, "category", "general")
        self.register(name, tool_obj, description=description, category=category)

    def import_from_tools_dict(self, tools_dict: dict[str, Callable]) -> None:
        """Import tools from the existing TOOLS dict format for backward compatibility."""
        for name, handler in tools_dict.items():
            self.register(name, handler, category="legacy")

    def merge(self, other: "ToolRegistry") -> None:
        """Merge another registry into this one."""
        for name, reg in other._tools.items():
            self.register(
                name, reg.handler,
                description=reg.description,
                category=reg.category,
                tags=reg.tags,
            )


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

registry: ToolRegistry = ToolRegistry()
