"""lookup_person tool — resolve a name to a canonical entity and find all appearances."""

from __future__ import annotations

import json

from playground.tools.base import ToolDefinition
from playground.tools import registry

_db = None


def init(db: object) -> None:
    global _db
    _db = db


def _lookup_person(name: str) -> dict:
    """Resolve a person's name (any alias) and return all cross-source appearances."""
    if _db is None:
        return {"error": "Database not initialized.", "found": False}

    entity_row = _db.get_entity_by_alias(name)
    if not entity_row:
        return {
            "found": False,
            "searched_name": name,
            "message": f"No entity found matching '{name}'. The name may not have been extracted during sync.",
        }

    aliases = _db.get_aliases(entity_row["id"])
    mention_rows = _db.get_mentions_for_entity(entity_row["id"])

    appearances = [
        {
            "document_id": row["document_id"],
            "source_type": row["source_type"],
            "title": row["title"],
            "excerpt": row["context_excerpt"],
            "deep_link": row["deep_link"],
            "score": 1.0,
        }
        for row in mention_rows
    ]

    return {
        "found": True,
        "canonical_name": entity_row["canonical_name"],
        "entity_type": entity_row["entity_type"],
        "aliases": aliases,
        "appearances": appearances,
        "total_appearances": len(appearances),
    }


_tool = ToolDefinition(
    name="lookup_person",
    description=(
        "Look up a person by name (or any known alias) and find all places they appear "
        "across indexed documents — Zoom transcripts, notes, and future sources. "
        "Use this for people-centric queries like 'what has Sarah said?' or 'find everything about John'."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The person's name or any known alias to look up.",
            },
        },
        "required": ["name"],
    },
    fn=_lookup_person,
)

registry.register(_tool)
