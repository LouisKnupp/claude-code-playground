"""list_meetings tool — browse Zoom meetings sorted by date with optional filters.

Unlike search_zoom (which requires a keyword), this tool lets the agent browse
meetings the way a person would: "show me everything with Kim, most recent first."
"""

from __future__ import annotations

import json

from playground.tools.base import ToolDefinition
from playground.tools import registry

_db = None


def init(db: object) -> None:
    """Inject the Database instance. Called from cli/main.py at startup."""
    global _db
    _db = db


def _list_meetings(
    participant: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 10,
) -> dict:
    """List Zoom meetings ordered by date (most recent first), with optional filters."""
    if _db is None:
        return {"error": "Database not initialized.", "results": []}

    rows = _db.list_meetings(
        participant=participant,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )

    results = []
    for row in rows:
        meta = json.loads(row["metadata_json"])
        results.append(
            {
                "document_id": row["id"],
                "title": row["title"],
                "meeting_date": meta.get("meeting_date", ""),
                "speakers": meta.get("speakers", []),
                "deep_link": row["deep_link"],
            }
        )

    return {"results": results, "total": len(results)}


_tool = ToolDefinition(
    name="list_meetings",
    description=(
        "List Zoom meetings sorted by date (most recent first). "
        "Use this to browse meetings without needing a keyword — for example, "
        "'find the most recent call with Kim' or 'show all meetings in January'. "
        "Supports filtering by participant name (resolved via the entity system) "
        "and date range. Returns document IDs you can pass to get_document for full transcripts."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "participant": {
                "type": "string",
                "description": "Optional: filter to meetings where this person was a participant.",
            },
            "date_from": {
                "type": "string",
                "description": "Optional: ISO date string (YYYY-MM-DD) — earliest meeting date.",
            },
            "date_to": {
                "type": "string",
                "description": "Optional: ISO date string (YYYY-MM-DD) — latest meeting date.",
            },
            "limit": {
                "type": "integer",
                "description": "Max number of meetings to return (default 10).",
                "default": 10,
            },
        },
        "required": [],
    },
    fn=_list_meetings,
)

registry.register(_tool)
