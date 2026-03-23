"""Entity extraction from documents during sync.

Calls the LLM to identify named entities (people, orgs, topics) in each document,
then stores them in the entity/alias/mention tables.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from playground.core.models import Document
from playground.core.roster import EmployeeRoster
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

Rules for "person" entities:
- ALWAYS use the person's full name (first + last) as "name". Never use a first name alone.
- If speaker names are provided in the document metadata, they are authoritative — use them as the
  canonical full name for that speaker. Include shorter forms (first name, nickname) as "aliases".
- If someone is referred to only by first name and you cannot resolve them to a full name from the
  speaker list or context, do NOT include them as a person entity.
- Do not create duplicate entities for the same person under different name forms; use aliases instead.
Only include entities you are confident about. Omit generic terms."""

_USER_TEMPLATE = """Document title: {title}{speaker_block}

{content}

Extract named entities from the text above."""

_SPEAKER_BLOCK = """
Known speakers (authoritative full names): {speakers}"""


def _is_first_name_only(name: str) -> bool:
    """Return True if the name appears to be a first name with no surname."""
    return " " not in name.strip()


def _resolve_to_full_name(
    first_name: str,
    aliases: list[str],
    speakers: list[str],
    roster: EmployeeRoster | None = None,
) -> str | None:
    """Try to promote a first-name-only entity to a full name.

    Resolution order (stops at first confident answer):
    1. A full-name alias supplied by the LLM (longest wins)
    2. The document's speaker list (unique match only)
    3. The employee roster (unique match only)

    Returns the full name string, or None if unresolvable / ambiguous.
    """
    # 1. Prefer the longest alias that looks like a full name
    full_name_aliases = [a for a in aliases if not _is_first_name_only(a)]
    if full_name_aliases:
        return max(full_name_aliases, key=len)

    first_lower = first_name.strip().lower()

    # 2. Speaker list: must be unambiguous
    speaker_matches = [s for s in speakers if s.split()[0].lower() == first_lower]
    if len(speaker_matches) == 1:
        return speaker_matches[0]

    # 3. Employee roster: must be unambiguous
    if roster:
        return roster.resolve_unique(first_name)

    return None


def extract_and_store(
    doc: Document,
    provider: LLMProvider,
    db: Database,
    roster: EmployeeRoster | None = None,
) -> int:
    """Extract entities from `doc` and persist to the database. Returns count stored."""
    # Truncate very long documents to avoid token limits
    content = doc.content_text[:6000]

    # Include speaker names when available (Zoom transcripts carry them in metadata)
    speakers: list[str] = doc.metadata.get("speakers", [])
    speaker_block = _SPEAKER_BLOCK.format(speakers=", ".join(speakers)) if speakers else ""

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _USER_TEMPLATE.format(
                title=doc.title, speaker_block=speaker_block, content=content
            ),
        },
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
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"LLM returned unparseable JSON: {exc}\nRaw response: {raw[:200]}") from exc

    stored = 0
    now = datetime.utcnow().isoformat()

    for ent in entities:
        if not isinstance(ent, dict) or not ent.get("name"):
            continue

        canonical = ent["name"].strip()
        entity_type = ent.get("type", "person")
        aliases: list[str] = [a.strip() for a in ent.get("aliases", []) if a.strip()]

        # For person entities: enforce full-name requirement.
        # If the LLM returned a first-name-only canonical, try to promote it
        # using aliases or the speaker list before accepting it.
        if entity_type == "person" and _is_first_name_only(canonical):
            resolved = _resolve_to_full_name(canonical, aliases, speakers, roster)
            if resolved:
                # Keep original first-name as an alias
                if canonical not in aliases:
                    aliases.append(canonical)
                canonical = resolved
            else:
                # Cannot resolve to a full name — skip this entity entirely
                continue

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
