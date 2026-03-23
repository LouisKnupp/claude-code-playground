"""Shared value types used across all layers. No internal playground imports."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Documents & indexing
# ---------------------------------------------------------------------------


@dataclass
class Document:
    """A single unit of content from any data source."""

    id: str                        # SHA256(source_id + content_text)
    source_type: str               # "zoom" | "apple_notes" | future
    title: str
    content_text: str              # clean plain text ready for FTS indexing
    metadata: dict[str, Any]       # connector-specific extras
    deep_link: str                 # clickable link back to source
    content_hash: str              # SHA256 of content_text for dedup
    indexed_at: datetime = field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------


@dataclass
class Entity:
    """A named entity (person, org, topic) normalized across sources."""

    id: str
    canonical_name: str
    entity_type: str               # "person" | "organization" | "topic"
    aliases: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class EntityMention:
    """A single occurrence of an entity within a document."""

    id: str
    entity_id: str
    document_id: str
    context_excerpt: str           # surrounding text shown to user
    offset_chars: int = 0


# ---------------------------------------------------------------------------
# Conversation & session
# ---------------------------------------------------------------------------


@dataclass
class ConversationMessage:
    """A single message in a conversation thread."""

    id: str
    session_id: str
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    turn_index: int = 0
    tool_call_id: str | None = None      # set for role="tool" messages
    tool_calls: list[dict] | None = None # set when assistant requests tool calls


# ---------------------------------------------------------------------------
# Tool results (returned by all search tools)
# ---------------------------------------------------------------------------


@dataclass
class ToolSearchResult:
    """A single result from any search tool."""

    document_id: str
    source_type: str
    title: str
    excerpt: str               # most relevant passage; always surfaced to user
    deep_link: str             # user can click/copy to verify
    score: float               # FTS5 relevance rank (higher = more relevant)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "source_type": self.source_type,
            "title": self.title,
            "excerpt": self.excerpt,
            "deep_link": self.deep_link,
            "score": self.score,
            "metadata": self.metadata,
        }


@dataclass
class PersonProfile:
    """Result of the lookup_person tool."""

    entity: Entity
    appearances: list[ToolSearchResult]

    def to_dict(self) -> dict:
        return {
            "canonical_name": self.entity.canonical_name,
            "entity_type": self.entity.entity_type,
            "aliases": self.entity.aliases,
            "appearances": [a.to_dict() for a in self.appearances],
        }


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


@dataclass
class ToolCallEntry:
    """Record of a single tool call made during an agent turn."""

    tool_name: str
    tool_args: dict[str, Any]
    tool_result: dict[str, Any]
    latency_ms: int
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_result": self.tool_result,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


@dataclass
class AuditEntry:
    """Complete record of one agent turn — everything the agent touched."""

    id: str
    session_id: str
    turn_index: int
    user_query: str
    final_response: str
    tool_calls: list[ToolCallEntry]
    full_message_thread: list[dict]    # raw OpenAI message dicts
    errors: list[str]
    latency_ms: int
    model_id: str
    created_at: datetime = field(default_factory=datetime.utcnow)

    def tool_calls_json(self) -> str:
        return json.dumps([tc.to_dict() for tc in self.tool_calls])

    def full_thread_json(self) -> str:
        return json.dumps(self.full_message_thread)

    def errors_json(self) -> str:
        return json.dumps(self.errors)


# ---------------------------------------------------------------------------
# Agent response (returned to CLI)
# ---------------------------------------------------------------------------


@dataclass
class AgentResponse:
    """The final output of one agent turn."""

    content: str
    sources: list[ToolSearchResult]
    tool_calls: list[ToolCallEntry]
