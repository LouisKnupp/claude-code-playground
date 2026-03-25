"""get_document tool — fetch the full transcript and metadata for a document by ID."""

from __future__ import annotations

import json

from playground.tools.base import ToolDefinition
from playground.tools import registry

_db = None


def init(db: object) -> None:
    """Inject the Database instance. Called from cli/main.py at startup."""
    global _db
    _db = db


def _get_document(document_id: str) -> dict:
    """Fetch the full transcript text and metadata for a specific document."""
    if _db is None:
        return {"error": "Database not initialized.", "found": False}

    row = _db.get_document(document_id)
    if not row:
        return {"found": False, "error": f"No document found with id '{document_id}'."}

    meta = json.loads(row["metadata_json"])
    return {
        "found": True,
        "document_id": row["id"],
        "source_type": row["source_type"],
        "title": row["title"],
        "content": row["content_text"],
        "deep_link": row["deep_link"],
        "speakers": meta.get("speakers", []),
        "meeting_date": meta.get("meeting_date", ""),
        "metadata": meta,
    }


_tool = ToolDefinition(
    name="get_document",
    description=(
        "Retrieve the full transcript and metadata for a specific document by its ID. "
        "Use this after search_zoom or list_meetings has identified the right document(s). "
        "Returns the complete transcript text so you can answer questions with full context."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "The document ID returned by search_zoom or list_meetings.",
            },
        },
        "required": ["document_id"],
    },
    fn=_get_document,
)

registry.register(_tool)
