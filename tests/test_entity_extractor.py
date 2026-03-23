"""Tests for entity extraction validation logic.

Covers the first-name-only resolution helpers and the full extract_and_store pipeline
with a fake LLM provider to confirm that ambiguous names are handled correctly.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from playground.core.models import Document
from playground.pipeline.entity_extractor import (
    _is_first_name_only,
    _resolve_to_full_name,
    extract_and_store,
)
from playground.providers.base import LLMProvider, LLMResponse
from playground.storage.db import Database



# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


def test_is_first_name_only_single_token():
    assert _is_first_name_only("Anthony") is True


def test_is_first_name_only_full_name():
    assert _is_first_name_only("Anthony Sims") is False


def test_is_first_name_only_aj():
    # "AJ" is two initials — still first-name-only by our rule
    assert _is_first_name_only("AJ") is True


def test_resolve_prefers_full_alias_over_speaker_list():
    # If an alias already has a space, prefer it without touching the speaker list
    result = _resolve_to_full_name("AJ", ["AJ Supinski"], speakers=["AJ Smith", "AJ Supinski"])
    assert result == "AJ Supinski"


def test_resolve_picks_longest_alias():
    result = _resolve_to_full_name("Tony", ["Tony S", "Tony Sims Jr"], speakers=[])
    assert result == "Tony Sims Jr"


def test_resolve_falls_back_to_speaker_list():
    result = _resolve_to_full_name("Anthony", aliases=[], speakers=["Anthony Sims"])
    assert result == "Anthony Sims"


def test_resolve_returns_none_when_ambiguous():
    # Two speakers share the first name "Anthony" — cannot resolve
    result = _resolve_to_full_name("Anthony", aliases=[], speakers=["Anthony Sims", "Anthony Brown"])
    assert result is None


def test_resolve_returns_none_when_no_match():
    result = _resolve_to_full_name("Anthony", aliases=[], speakers=["Louis Knupp", "Abbie Paul"])
    assert result is None


# ---------------------------------------------------------------------------
# Fake LLM provider for integration tests
# ---------------------------------------------------------------------------


class FakeLLMProvider:
    """Returns a pre-configured JSON payload regardless of input."""

    model_id = "fake"

    def __init__(self, response_json: list[dict]):
        self._response = json.dumps(response_json)

    def complete(self, messages: list[dict]) -> LLMResponse:
        return LLMResponse(content=self._response, finish_reason="stop")

    def complete_with_tools(self, messages, tools):  # pragma: no cover
        raise NotImplementedError

    def stream_complete(self, messages):  # pragma: no cover
        raise NotImplementedError


def _make_doc(content: str, speakers: list[str] | None = None) -> Document:
    return Document(
        id="test-doc-1",
        source_type="zoom",
        title="Test Meeting",
        content_text=content,
        metadata={"speakers": speakers or []},
        deep_link="file:///fake/path.txt",
        content_hash="abc123",
        indexed_at=datetime.utcnow(),
    )


def _open_db() -> Database:
    tmp = tempfile.mktemp(suffix=".db")
    return Database(Path(tmp))


def _insert_doc(db: Database, doc: Document) -> None:
    """Insert a Document into the DB so FK constraints on entity_mentions pass."""
    db.upsert_document(
        id=doc.id,
        source_type=doc.source_type,
        title=doc.title,
        content_text=doc.content_text,
        metadata_json=json.dumps(doc.metadata),
        deep_link=doc.deep_link,
        content_hash=doc.content_hash,
        indexed_at=doc.indexed_at.isoformat(),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Integration tests: extract_and_store with fake LLM
# ---------------------------------------------------------------------------


def test_full_name_entity_stored_normally():
    """A well-formed full-name entity is stored as-is."""
    db = _open_db()
    provider = FakeLLMProvider([
        {
            "name": "Anthony Sims",
            "type": "person",
            "aliases": ["Anthony"],
            "mentions": [{"excerpt": "Anthony Sims joined the call", "offset": 0}],
        }
    ])
    doc = _make_doc("Anthony Sims joined the call", speakers=["Anthony Sims"])
    _insert_doc(db, doc)
    count = extract_and_store(doc, provider, db)
    assert count == 1
    entity = db.get_entity_by_alias("Anthony Sims")
    assert entity is not None
    assert entity["canonical_name"] == "Anthony Sims"
    # "Anthony" should be stored as an alias
    aliases = db.get_aliases(entity["id"])
    assert "Anthony" in aliases


def test_first_name_only_promoted_via_alias():
    """If the LLM returns first-name-only but includes a full-name alias, promote it."""
    db = _open_db()
    provider = FakeLLMProvider([
        {
            "name": "AJ",
            "type": "person",
            "aliases": ["AJ Supinski"],
            "mentions": [{"excerpt": "AJ presented the update", "offset": 0}],
        }
    ])
    doc = _make_doc("AJ presented the update", speakers=["AJ Supinski"])
    _insert_doc(db, doc)
    count = extract_and_store(doc, provider, db)
    assert count == 1
    entity = db.get_entity_by_alias("AJ Supinski")
    assert entity is not None
    assert entity["canonical_name"] == "AJ Supinski"
    aliases = db.get_aliases(entity["id"])
    assert "AJ" in aliases


def test_first_name_only_resolved_via_speaker_list():
    """First-name-only canonical resolved unambiguously from speaker metadata."""
    db = _open_db()
    provider = FakeLLMProvider([
        {
            "name": "Anthony",
            "type": "person",
            "aliases": [],
            "mentions": [{"excerpt": "Anthony shared the screen", "offset": 0}],
        }
    ])
    doc = _make_doc("Anthony shared the screen", speakers=["Anthony Sims"])
    _insert_doc(db, doc)
    count = extract_and_store(doc, provider, db)
    assert count == 1
    entity = db.get_entity_by_alias("Anthony Sims")
    assert entity is not None
    assert entity["canonical_name"] == "Anthony Sims"


def test_ambiguous_first_name_is_discarded():
    """When two speakers share a first name, the entity is dropped entirely."""
    db = _open_db()
    provider = FakeLLMProvider([
        {
            "name": "Anthony",
            "type": "person",
            "aliases": [],
            "mentions": [{"excerpt": "Anthony joined late", "offset": 0}],
        }
    ])
    doc = _make_doc(
        "Anthony joined late",
        speakers=["Anthony Sims", "Anthony Brown"],
    )
    _insert_doc(db, doc)
    count = extract_and_store(doc, provider, db)
    assert count == 0
    assert db.get_entity_by_alias("Anthony") is None


def test_unresolvable_first_name_is_discarded():
    """A first-name-only entity with no speaker match is silently dropped."""
    db = _open_db()
    provider = FakeLLMProvider([
        {
            "name": "Bob",
            "type": "person",
            "aliases": [],
            "mentions": [{"excerpt": "Bob mentioned it", "offset": 0}],
        }
    ])
    doc = _make_doc("Bob mentioned it", speakers=["Louis Knupp", "Abbie Paul"])
    _insert_doc(db, doc)
    count = extract_and_store(doc, provider, db)
    assert count == 0
    assert db.get_entity_by_alias("Bob") is None


def test_non_person_entity_not_affected_by_first_name_rule():
    """Organizations and topics with single-word names should pass through unchanged."""
    db = _open_db()
    provider = FakeLLMProvider([
        {
            "name": "Slack",
            "type": "organization",
            "aliases": [],
            "mentions": [{"excerpt": "We use Slack for comms", "offset": 0}],
        }
    ])
    doc = _make_doc("We use Slack for comms")
    _insert_doc(db, doc)
    count = extract_and_store(doc, provider, db)
    assert count == 1
    entity = db.get_entity_by_alias("Slack")
    assert entity is not None


def test_extract_and_store_reuses_existing_entity_without_alias(tmp_path):
    """Entity exists in DB but canonical alias row is missing — aliases and mentions should still be added."""
    db = Database(tmp_path / "playground.db")
    now = datetime.utcnow().isoformat()

    db.upsert_document(
        id="doc-1",
        source_type="zoom",
        title="Anthony / Louis",
        content_text="Anthony and Louis discussed the project.",
        metadata_json="{}",
        deep_link="file:///tmp/doc-1.txt",
        content_hash="hash-1",
        indexed_at=now,
    )

    # Simulate an older/bad state: entity exists, but canonical alias row is missing.
    db.upsert_entity("entity-1", "Anthony / Louis", "person", now)
    db.commit()

    provider = FakeLLMProvider([
        {
            "name": "Anthony / Louis",
            "type": "person",
            "aliases": ["Anthony and Louis"],
            "mentions": [{"excerpt": "Anthony and Louis discussed the project.", "offset": 0}],
        }
    ])

    doc = Document(
        id="doc-1",
        source_type="zoom",
        title="Anthony / Louis",
        content_text="Anthony and Louis discussed the project.",
        metadata={},
        deep_link="file:///tmp/doc-1.txt",
        content_hash="hash-1",
        indexed_at=datetime.utcnow(),
    )

    stored = extract_and_store(doc, provider, db)

    assert stored == 1

    aliases = db.get_aliases("entity-1")
    assert "Anthony / Louis" in aliases
    assert "Anthony and Louis" in aliases

    mentions = db.get_mentions_for_entity("entity-1")
    assert len(mentions) == 1
