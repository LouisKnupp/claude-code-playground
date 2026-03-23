"""search_notes tool — FTS5 search over indexed Apple Notes."""

from __future__ import annotations

import json

from playground.tools.base import ToolDefinition
from playground.tools import registry

_db = None


def init(db: object) -> None:
    global _db
    _db = db


def _search_notes(
    query: str,
    person_mentioned: str | None = None,
    top_k: int = 5,
) -> dict:
    """Search Apple Notes by keyword. Returns excerpts + notes:// deep links."""
    if _db is None:
        return {"error": "Database not initialized.", "results": []}

    rows = _db.search_fts(query=query, source_type="apple_notes", top_k=top_k * 3)

    results = []
    for row in rows:
        # Optional person filter via entity mentions (post-FTS)
        if person_mentioned:
            entity_row = _db.get_entity_by_alias(person_mentioned)
            if entity_row:
                mentions = _db.get_mentions_for_entity(entity_row["id"])
                mentioned_in = {m["document_id"] for m in mentions}
                if row["id"] not in mentioned_in:
                    continue

        results.append(
            {
                "document_id": row["id"],
                "source_type": "apple_notes",
                "title": row["title"],
                "excerpt": row["excerpt"],
                "deep_link": row["deep_link"],
                "score": row["score"],
            }
        )
        if len(results) >= top_k:
            break

    return {"results": results, "total": len(results)}


_tool = ToolDefinition(
    name="search_notes",
    description=(
        "Search through indexed Apple Notes. Use this to find information from your personal "
        "notes, project notes, meeting notes, or any other notes you've written. "
        "Returns excerpts with a link to open the note directly in the Notes app."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords or phrase to search for in notes.",
            },
            "person_mentioned": {
                "type": "string",
                "description": "Optional: filter to notes that mention a specific person.",
            },
            "top_k": {
                "type": "integer",
                "description": "Max number of results to return (default 5).",
                "default": 5,
            },
        },
        "required": ["query"],
    },
    fn=_search_notes,
)

registry.register(_tool)
