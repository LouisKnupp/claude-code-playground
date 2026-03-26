"""Tests for get_document, list_meetings tools and related DB methods."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from playground.storage.db import Database
import playground.tools.get_document as get_document_mod
import playground.tools.list_meetings as list_meetings_mod
import playground.tools.search_zoom as search_zoom_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def _insert_meeting(
    db: Database,
    doc_id: str,
    title: str,
    content: str,
    speakers: list[str],
    meeting_date: str,
    deep_link: str = "file:///fake",
) -> None:
    meta = json.dumps({"speakers": speakers, "meeting_date": meeting_date})
    db.upsert_document(
        id=doc_id,
        source_type="zoom",
        title=title,
        content_text=content,
        metadata_json=meta,
        deep_link=deep_link,
        content_hash=doc_id,
        indexed_at=datetime.utcnow().isoformat(),
    )
    db.commit()


def _add_person(db: Database, doc_id: str, canonical: str, aliases: list[str]) -> str:
    import uuid
    entity_id = str(uuid.uuid4())
    db.upsert_entity(entity_id, canonical, "person", datetime.utcnow().isoformat())
    for alias in aliases:
        db.add_alias(alias, entity_id)
    db.upsert_mention(
        str(uuid.uuid4()), entity_id, doc_id, f"{canonical} said something.", 0
    )
    db.commit()
    return entity_id


# ---------------------------------------------------------------------------
# db.get_document
# ---------------------------------------------------------------------------

class TestGetDocumentDB:
    def test_returns_full_content(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-1", "Weekly Sync", "Full transcript text here.", ["Alice"], "2026-01-15 10:00:00")
        row = db.get_document("doc-1")
        assert row is not None
        assert row["content_text"] == "Full transcript text here."
        assert row["title"] == "Weekly Sync"

    def test_returns_none_for_missing_id(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.get_document("nonexistent") is None


# ---------------------------------------------------------------------------
# db.list_meetings
# ---------------------------------------------------------------------------

class TestListMeetingsDB:
    def test_returns_all_zoom_docs_sorted_by_date_desc(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-a", "Old Meeting", "content", [], "2026-01-01 09:00:00")
        _insert_meeting(db, "doc-b", "Recent Meeting", "content", [], "2026-03-01 09:00:00")
        _insert_meeting(db, "doc-c", "Middle Meeting", "content", [], "2026-02-01 09:00:00")

        rows = db.list_meetings()
        ids = [r["id"] for r in rows]
        assert ids == ["doc-b", "doc-c", "doc-a"]

    def test_date_from_filter(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-old", "Old", "content", [], "2026-01-01 09:00:00")
        _insert_meeting(db, "doc-new", "New", "content", [], "2026-03-01 09:00:00")

        rows = db.list_meetings(date_from="2026-02-01")
        assert len(rows) == 1
        assert rows[0]["id"] == "doc-new"

    def test_date_to_filter(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-old", "Old", "content", [], "2026-01-01 09:00:00")
        _insert_meeting(db, "doc-new", "New", "content", [], "2026-03-01 09:00:00")

        rows = db.list_meetings(date_to="2026-01-31")
        assert len(rows) == 1
        assert rows[0]["id"] == "doc-old"

    def test_date_from_and_to_filter(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-1", "Jan", "content", [], "2026-01-15 09:00:00")
        _insert_meeting(db, "doc-2", "Feb", "content", [], "2026-02-15 09:00:00")
        _insert_meeting(db, "doc-3", "Mar", "content", [], "2026-03-15 09:00:00")

        rows = db.list_meetings(date_from="2026-02-01", date_to="2026-02-28")
        assert len(rows) == 1
        assert rows[0]["id"] == "doc-2"

    def test_participant_filter_via_entity_system(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-kim", "Kim's meeting", "content", ["Kim Johnson"], "2026-03-01 09:00:00")
        _insert_meeting(db, "doc-bob", "Bob's meeting", "content", ["Bob Smith"], "2026-03-02 09:00:00")
        _add_person(db, "doc-kim", "Kim Johnson", ["Kim Johnson", "Kim"])

        rows = db.list_meetings(participant="Kim")
        assert len(rows) == 1
        assert rows[0]["id"] == "doc-kim"

    def test_participant_filter_matches_alias(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-1", "Meeting A", "content", [], "2026-03-01 09:00:00")
        _insert_meeting(db, "doc-2", "Meeting B", "content", [], "2026-03-02 09:00:00")
        _add_person(db, "doc-1", "Kimberly Johnson", ["Kimberly Johnson", "Kim J", "Kim"])

        rows = db.list_meetings(participant="Kimberly")
        assert len(rows) == 1
        assert rows[0]["id"] == "doc-1"

    def test_limit_respected(self, tmp_path):
        db = _make_db(tmp_path)
        for i in range(5):
            _insert_meeting(db, f"doc-{i}", f"Meeting {i}", "content", [], f"2026-0{i+1}-01 09:00:00")

        rows = db.list_meetings(limit=3)
        assert len(rows) == 3

    def test_excludes_non_zoom_docs(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-zoom", "Zoom Meeting", "content", [], "2026-03-01 09:00:00")
        # Insert a notes document directly
        db.upsert_document(
            id="doc-note",
            source_type="notes",
            title="A Note",
            content_text="some note",
            metadata_json="{}",
            deep_link="",
            content_hash="note-hash",
            indexed_at=datetime.utcnow().isoformat(),
        )
        db.commit()

        rows = db.list_meetings()
        ids = [r["id"] for r in rows]
        assert "doc-zoom" in ids
        assert "doc-note" not in ids


# ---------------------------------------------------------------------------
# get_document tool
# ---------------------------------------------------------------------------

class TestGetDocumentTool:
    def test_returns_full_transcript(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-1", "Sprint Review", "Louis: Let's ship it.", ["Louis"], "2026-03-10 14:00:00")
        get_document_mod.init(db)

        result = get_document_mod._get_document("doc-1")
        assert result["found"] is True
        assert result["content"] == "Louis: Let's ship it."
        assert result["title"] == "Sprint Review"
        assert result["meeting_date"] == "2026-03-10 14:00:00"
        assert "Louis" in result["speakers"]

    def test_not_found_returns_error(self, tmp_path):
        db = _make_db(tmp_path)
        get_document_mod.init(db)

        result = get_document_mod._get_document("ghost-id")
        assert result["found"] is False
        assert "error" in result

    def test_no_db_returns_error(self):
        get_document_mod._db = None
        result = get_document_mod._get_document("anything")
        assert "error" in result


# ---------------------------------------------------------------------------
# list_meetings tool
# ---------------------------------------------------------------------------

class TestListMeetingsTool:
    def test_returns_meetings_sorted_by_date(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-1", "Jan Meeting", "content", [], "2026-01-10 09:00:00")
        _insert_meeting(db, "doc-2", "Mar Meeting", "content", [], "2026-03-10 09:00:00")
        list_meetings_mod.init(db)

        result = list_meetings_mod._list_meetings()
        assert result["results"][0]["document_id"] == "doc-2"
        assert result["results"][1]["document_id"] == "doc-1"

    def test_result_shape(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-1", "Team Sync", "content", ["Alice", "Bob"], "2026-03-01 09:00:00", "file:///team")
        list_meetings_mod.init(db)

        result = list_meetings_mod._list_meetings()
        r = result["results"][0]
        assert r["document_id"] == "doc-1"
        assert r["title"] == "Team Sync"
        assert r["meeting_date"] == "2026-03-01 09:00:00"
        assert set(r["speakers"]) == {"Alice", "Bob"}
        assert r["deep_link"] == "file:///team"

    def test_participant_filter(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-kim", "Kim's call", "content", [], "2026-03-01 09:00:00")
        _insert_meeting(db, "doc-bob", "Bob's call", "content", [], "2026-03-02 09:00:00")
        _add_person(db, "doc-kim", "Kim Johnson", ["Kim Johnson", "Kim"])
        list_meetings_mod.init(db)

        result = list_meetings_mod._list_meetings(participant="Kim")
        assert result["total"] == 1
        assert result["results"][0]["document_id"] == "doc-kim"

    def test_date_range_filter(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-jan", "Jan", "content", [], "2026-01-15 09:00:00")
        _insert_meeting(db, "doc-mar", "Mar", "content", [], "2026-03-15 09:00:00")
        list_meetings_mod.init(db)

        result = list_meetings_mod._list_meetings(date_from="2026-03-01", date_to="2026-03-31")
        assert result["total"] == 1
        assert result["results"][0]["document_id"] == "doc-mar"

    def test_no_db_returns_error(self):
        list_meetings_mod._db = None
        result = list_meetings_mod._list_meetings()
        assert "error" in result


# ---------------------------------------------------------------------------
# search_zoom date filtering (regression: was defined but not applied)
# ---------------------------------------------------------------------------

class TestSearchZoomDateFilter:
    def test_date_from_excludes_old_meetings(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-old", "Old Meeting", "budget planning review", [], "2026-01-01 09:00:00")
        _insert_meeting(db, "doc-new", "New Meeting", "budget planning review", [], "2026-03-01 09:00:00")
        search_zoom_mod.init(db)

        result = search_zoom_mod._search_zoom(query="budget planning", date_from="2026-02-01")
        ids = [r["document_id"] for r in result["results"]]
        assert "doc-new" in ids
        assert "doc-old" not in ids

    def test_date_to_excludes_future_meetings(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-old", "Old Meeting", "quarterly roadmap review", [], "2026-01-01 09:00:00")
        _insert_meeting(db, "doc-new", "New Meeting", "quarterly roadmap review", [], "2026-03-01 09:00:00")
        search_zoom_mod.init(db)

        result = search_zoom_mod._search_zoom(query="quarterly roadmap", date_to="2026-01-31")
        ids = [r["document_id"] for r in result["results"]]
        assert "doc-old" in ids
        assert "doc-new" not in ids

    def test_results_include_meeting_date(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_meeting(db, "doc-1", "Meeting", "sprint retrospective notes", [], "2026-03-10 09:00:00")
        search_zoom_mod.init(db)

        result = search_zoom_mod._search_zoom(query="sprint retrospective")
        assert result["results"][0]["meeting_date"] == "2026-03-10 09:00:00"
