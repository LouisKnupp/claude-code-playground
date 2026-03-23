"""search_zoom tool — FTS5 search over indexed Zoom transcripts.

The database instance is injected at startup via init().
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


def _search_zoom(
    query: str,
    speaker: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    top_k: int = 5,
) -> dict:
    """Search Zoom transcripts by keyword. Returns excerpts + deep links."""
    if _db is None:
        return {"error": "Database not initialized.", "results": []}

    rows = _db.search_fts(query=query, source_type="zoom", top_k=top_k * 3)

    results = []
    for row in rows:
        meta = json.loads(row["metadata_json"])

        # Optional speaker filter (post-FTS)
        if speaker:
            speakers = [s.lower() for s in meta.get("speakers", [])]
            if not any(speaker.lower() in s for s in speakers):
                continue

        results.append(
            {
                "document_id": row["id"],
                "source_type": "zoom",
                "title": row["title"],
                "excerpt": row["excerpt"],
                "deep_link": row["deep_link"],
                "score": row["score"],
                "speakers": meta.get("speakers", []),
                "first_timestamp": meta.get("first_timestamp", ""),
                "filename": meta.get("filename", ""),
            }
        )
        if len(results) >= top_k:
            break

    return {"results": results, "total": len(results)}


_tool = ToolDefinition(
    name="search_zoom",
    description=(
        "Search through indexed Zoom meeting transcripts. Use this to find what was discussed "
        "in meetings, who said what, or anything from a recorded call. "
        "Returns excerpts from the transcript with a link to the full file."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords or phrase to search for in transcripts.",
            },
            "speaker": {
                "type": "string",
                "description": "Optional: filter results to a specific speaker name.",
            },
            "date_from": {
                "type": "string",
                "description": "Optional: ISO date string (YYYY-MM-DD) — earliest meeting date.",
            },
            "date_to": {
                "type": "string",
                "description": "Optional: ISO date string (YYYY-MM-DD) — latest meeting date.",
            },
            "top_k": {
                "type": "integer",
                "description": "Max number of results to return (default 5).",
                "default": 5,
            },
        },
        "required": ["query"],
    },
    fn=_search_zoom,
)

registry.register(_tool)
