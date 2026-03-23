"""Tool registry — mirrors providers/registry.py pattern.

Tool modules call register() at import time. main.py imports this module,
then each tool module, which triggers self-registration.

Usage:
    from playground.tools import registry
    import playground.tools.search_zoom    # triggers registration
    import playground.tools.search_notes   # triggers registration

    specs = registry.get_all_openai_specs()   # pass to LLM
    result = registry.execute("search_zoom", {"query": "standup"})
"""

from __future__ import annotations

from typing import Any

from playground.core.exceptions import ToolError
from playground.tools.base import ToolDefinition

_registry: dict[str, ToolDefinition] = {}


def register(tool: ToolDefinition) -> None:
    _registry[tool.name] = tool


def get_all_definitions() -> list[ToolDefinition]:
    return list(_registry.values())


def get_all_openai_specs() -> list[dict]:
    """Return all tools formatted for the OpenAI tools parameter."""
    return [t.to_openai_spec() for t in _registry.values()]


def execute(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call a registered tool by name with keyword arguments."""
    if name not in _registry:
        raise ToolError(name, f"Tool '{name}' not registered.")
    try:
        return _registry[name].fn(**args)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(name, str(exc)) from exc


def available() -> list[str]:
    return list(_registry.keys())
