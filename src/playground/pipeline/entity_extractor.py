"""Entity extraction from documents during sync.

Calls the LLM to identify named entities (people, orgs, topics) in each document,
then stores them in the entity/alias/mention tables.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from playground.core.models import Document
from playground.providers.base import LLMProvider
from playground.storage.db import Database

_SYSTEM_PROMPT = """You are an entity extraction assistant. Given document text, extract named entities.
Return a JSON array (and nothing else) with this structure:
[
  {
    "name": "Full Name",
    "type": "person",
    "aliases": ["Nick", "Short Name"],
    "mentions": [
      {"excerpt": "...surrounding text...", "offset": 0}
    ]
  }
]
Types: "person", "organization", "topic".
Only include entities you are confident about. Omit generic terms."""

_USER_TEMPLATE = """Document title: {title}

{content}

Extract named entities from the text above."""


def extract_and_store(doc: Document, provider: LLMProvider, db: Database) -> int:
    """Extract entities from `doc` and persist to the database. Returns count stored."""
    # Truncate very long documents to avoid token limits
    content = doc.content_text[:6000]

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _USER_TEMPLATE.format(title=doc.title, content=content)},
    ]

    try:
        response = provider.complete(messages)
        raw = response.content.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        entities = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return 0  # Extraction failed — not fatal, doc is still indexed

    stored = 0
    now = datetime.utcnow().isoformat()

    for ent in entities:
        if not isinstance(ent, dict) or not ent.get("name"):
            continue

        canonical = ent["name"].strip()
        entity_type = ent.get("type", "person")
        aliases: list[str] = [a.strip() for a in ent.get("aliases", []) if a.strip()]

        # Check if a matching entity already exists (via any alias or canonical name)
        existing = db.get_entity_by_alias(canonical)
        if existing:
            entity_id = existing["id"]
        else:
            entity_id = str(uuid.uuid4())
            db.upsert_entity(entity_id, canonical, entity_type, now)
            db.add_alias(canonical, entity_id)

        # Add any new aliases
        for alias in aliases:
            db.add_alias(alias, entity_id)

        # Store mentions
        for mention in ent.get("mentions", []):
            excerpt = mention.get("excerpt", "")
            offset = mention.get("offset", 0)
            if excerpt:
                db.upsert_mention(
                    id=str(uuid.uuid4()),
                    entity_id=entity_id,
                    document_id=doc.id,
                    context_excerpt=excerpt,
                    offset_chars=offset,
                )

        stored += 1

    db.commit()
    return stored
