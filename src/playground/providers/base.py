"""LLM provider protocol.

Any provider (OpenAI, Anthropic, Google, …) must implement LLMProvider.
The protocol is runtime_checkable so isinstance() works in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, runtime_checkable


@dataclass
class ToolCall:
    """A single tool invocation requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Parsed response from a provider call."""

    content: str                          # text content (may be empty if tool_calls present)
    finish_reason: str                    # "stop" | "tool_calls" | "length" | "error"
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None                       # provider-specific raw response for debugging


@runtime_checkable
class LLMProvider(Protocol):
    """Contract all LLM providers must satisfy."""

    @property
    def model_id(self) -> str:
        """The full model identifier string (e.g. 'gpt-5.4')."""
        ...

    def complete(self, messages: list[dict]) -> LLMResponse:
        """Single-turn completion without tools."""
        ...

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> LLMResponse:
        """Completion with tool definitions; may return tool_calls."""
        ...

    def stream_complete(self, messages: list[dict]) -> Iterator[str]:
        """Streaming completion; yields text chunks."""
        ...
