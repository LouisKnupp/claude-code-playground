"""Tool definition and registry base types.

Each tool is a ToolDefinition: a name, description, JSON Schema for parameters,
and a callable that returns a JSON-serializable dict.

The parameters_schema is passed directly to the OpenAI tools API, so it must
follow the format OpenAI expects:
  {"type": "object", "properties": {...}, "required": [...]}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToolDefinition:
    """Everything needed to expose a tool to the LLM."""

    name: str
    description: str
    parameters_schema: dict[str, Any]   # JSON Schema object
    fn: Callable[..., dict[str, Any]]   # called with kwargs matching the schema

    def to_openai_spec(self) -> dict:
        """Format for OpenAI's tools parameter."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }
