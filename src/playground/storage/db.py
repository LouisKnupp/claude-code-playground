"""SQLite database layer.

All tables are created here. FTS5 virtual table is used for full-text search.
A single connection is held open per DB instance for the lifetime of a process.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from playground.core.exceptions import StorageError


_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- -----------------------------------------------------------------------
-- Documents
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    source_type  TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    content_text TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    deep_link    TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    indexed_at   TEXT NOT NULL
);

-- FTS5 virtual table backed by the documents table
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title,
    content_text,
    content=documents,
    content_rowid=rowid,
    tokenize='porter unicode61'
);

-- Keep FTS in sync with documents
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, title, content_text)
    VALUES (new.rowid, new.title, new.content_text);
END;

CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, content_text)
    VALUES ('delete', old.rowid, old.title, old.content_text);
END;

CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, content_text)
    VALUES ('delete', old.rowid, old.title, old.content_text);
    INSERT INTO documents_fts(rowid, title, content_text)
    VALUES (new.rowid, new.title, new.content_text);
END;

-- -----------------------------------------------------------------------
-- Entity system
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entities (
    id             TEXT PRIMARY KEY,
    canonical_name TEXT UNIQUE NOT NULL,
    entity_type    TEXT NOT NULL DEFAULT 'person',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_aliases (
    alias     TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (alias, entity_id)
);

CREATE TABLE IF NOT EXISTS entity_mentions (
    id               TEXT PRIMARY KEY,
    entity_id        TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    document_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    context_excerpt  TEXT NOT NULL DEFAULT '',
    offset_chars     INTEGER NOT NULL DEFAULT 0
);

-- -----------------------------------------------------------------------
-- Conversation history
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversation_messages (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL DEFAULT '',
    tool_call_id TEXT,
    tool_calls_json TEXT,
    created_at   TEXT NOT NULL,
    turn_index   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON conversation_messages(session_id, turn_index);

-- -----------------------------------------------------------------------
-- Audit log
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_entries (
    id                      TEXT PRIMARY KEY,
    session_id              TEXT NOT NULL,
    turn_index              INTEGER NOT NULL DEFAULT 0,
    user_query              TEXT NOT NULL DEFAULT '',
    final_response          TEXT NOT NULL DEFAULT '',
    tool_calls_json         TEXT NOT NULL DEFAULT '[]',
    full_message_thread_json TEXT NOT NULL DEFAULT '[]',
    errors_json             TEXT NOT NULL DEFAULT '[]',
    latency_ms              INTEGER NOT NULL DEFAULT 0,
    model_id                TEXT NOT NULL DEFAULT '',
    created_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_session
    ON audit_entries(session_id, turn_index);
"""


class Database:
    """Thin wrapper around a SQLite connection."""

    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            self._conn = sqlite3.connect(str(path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to open database at {path}: {exc}") from exc

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        try:
            return self._conn.execute(sql, params)
        except sqlite3.Error as exc:
            raise StorageError(f"Query failed: {exc}\nSQL: {sql}") from exc

    def executemany(self, sql: str, params_seq: list[tuple]) -> None:
        try:
            self._conn.executemany(sql, params_seq)
        except sqlite3.Error as exc:
            raise StorageError(f"Batch query failed: {exc}\nSQL: {sql}") from exc

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Document helpers
    # ------------------------------------------------------------------

    def upsert_document(
        self,
        id: str,
        source_type: str,
        title: str,
        content_text: str,
        metadata_json: str,
        deep_link: str,
        content_hash: str,
        indexed_at: str,
    ) -> None:
        self.execute(
            """
            INSERT INTO documents
                (id, source_type, title, content_text, metadata_json, deep_link, content_hash, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title        = excluded.title,
                content_text = excluded.content_text,
                metadata_json = excluded.metadata_json,
                deep_link    = excluded.deep_link,
                content_hash = excluded.content_hash,
                indexed_at   = excluded.indexed_at
            """,
            (id, source_type, title, content_text, metadata_json, deep_link, content_hash, indexed_at),
        )

    def get_existing_hashes(self, source_type: str) -> dict[str, str]:
        """Return {document_id: content_hash} for all docs of a given source type."""
        rows = self.execute(
            "SELECT id, content_hash FROM documents WHERE source_type = ?",
            (source_type,),
        ).fetchall()
        return {row["id"]: row["content_hash"] for row in rows}

    def search_fts(
        self,
        query: str,
        source_type: str | None,
        top_k: int,
    ) -> list[sqlite3.Row]:
        """Full-text search using FTS5. Returns rows ordered by rank."""
        if source_type:
            return self.execute(
                """
                SELECT d.id, d.source_type, d.title, d.deep_link, d.metadata_json,
                       snippet(documents_fts, 1, '[', ']', '…', 32) AS excerpt,
                       bm25(documents_fts) AS score
                FROM documents_fts
                JOIN documents d ON d.rowid = documents_fts.rowid
                WHERE documents_fts MATCH ?
                  AND d.source_type = ?
                ORDER BY score
                LIMIT ?
                """,
                (query, source_type, top_k),
            ).fetchall()
        return self.execute(
            """
            SELECT d.id, d.source_type, d.title, d.deep_link, d.metadata_json,
                   snippet(documents_fts, 1, '[', ']', '…', 32) AS excerpt,
                   bm25(documents_fts) AS score
            FROM documents_fts
            JOIN documents d ON d.rowid = documents_fts.rowid
            WHERE documents_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (query, top_k),
        ).fetchall()

    # ------------------------------------------------------------------
    # Entity helpers
    # ------------------------------------------------------------------

    def upsert_entity(self, id: str, canonical_name: str, entity_type: str, created_at: str) -> None:
        self.execute(
            """
            INSERT INTO entities (id, canonical_name, entity_type, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(canonical_name) DO NOTHING
            """,
            (id, canonical_name, entity_type, created_at),
        )

    def get_entity_by_alias(self, alias: str) -> sqlite3.Row | None:
        return self.execute(
            """
            SELECT e.* FROM entities e
            JOIN entity_aliases a ON a.entity_id = e.id
            WHERE lower(a.alias) = lower(?)
            LIMIT 1
            """,
            (alias,),
        ).fetchone()

    def get_entity_by_canonical_name(self, canonical_name: str) -> sqlite3.Row | None:
        return self.execute(
            """
            SELECT * FROM entities
            WHERE lower(canonical_name) = lower(?)
            LIMIT 1
            """,
            (canonical_name,),
        ).fetchone()

    def add_alias(self, alias: str, entity_id: str) -> None:
        self.execute(
            "INSERT OR IGNORE INTO entity_aliases (alias, entity_id) VALUES (?, ?)",
            (alias, entity_id),
        )

    def get_aliases(self, entity_id: str) -> list[str]:
        rows = self.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id = ?", (entity_id,)
        ).fetchall()
        return [r["alias"] for r in rows]

    def upsert_mention(
        self, id: str, entity_id: str, document_id: str, context_excerpt: str, offset_chars: int
    ) -> None:
        self.execute(
            """
            INSERT OR IGNORE INTO entity_mentions
                (id, entity_id, document_id, context_excerpt, offset_chars)
            VALUES (?, ?, ?, ?, ?)
            """,
            (id, entity_id, document_id, context_excerpt, offset_chars),
        )

    def get_mentions_for_entity(self, entity_id: str) -> list[sqlite3.Row]:
        return self.execute(
            """
            SELECT em.*, d.source_type, d.title, d.deep_link, d.metadata_json
            FROM entity_mentions em
            JOIN documents d ON d.id = em.document_id
            WHERE em.entity_id = ?
            ORDER BY d.indexed_at DESC
            """,
            (entity_id,),
        ).fetchall()

    def list_person_entities(self) -> list[sqlite3.Row]:
        """Return all entities of type 'person', ordered by canonical_name."""
        return self.execute(
            "SELECT * FROM entities WHERE entity_type = 'person' ORDER BY canonical_name"
        ).fetchall()

    def get_entity_by_id(self, entity_id: str) -> sqlite3.Row | None:
        return self.execute(
            "SELECT * FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()

    def get_entity_documents(self, entity_id: str) -> list[sqlite3.Row]:
        """Return distinct documents (with metadata_json) where an entity is mentioned."""
        return self.execute(
            """
            SELECT DISTINCT d.id, d.source_type, d.title, d.metadata_json
            FROM entity_mentions em
            JOIN documents d ON d.id = em.document_id
            WHERE em.entity_id = ?
            """,
            (entity_id,),
        ).fetchall()

    def update_canonical_name(self, entity_id: str, new_name: str) -> None:
        self.execute(
            "UPDATE entities SET canonical_name = ? WHERE id = ?",
            (new_name, entity_id),
        )

    def merge_entity_into(self, keep_id: str, discard_id: str) -> None:
        """Merge discard_id into keep_id: move aliases and mentions, then delete discard."""
        # Move aliases (INSERT OR IGNORE skips dupes)
        self.execute(
            "UPDATE OR IGNORE entity_aliases SET entity_id = ? WHERE entity_id = ?",
            (keep_id, discard_id),
        )
        # Delete any remaining aliases that couldn't be moved (exact dupes)
        self.execute(
            "DELETE FROM entity_aliases WHERE entity_id = ?",
            (discard_id,),
        )
        # Move mentions
        self.execute(
            "UPDATE entity_mentions SET entity_id = ? WHERE entity_id = ?",
            (keep_id, discard_id),
        )
        # Delete the now-empty entity
        self.execute("DELETE FROM entities WHERE id = ?", (discard_id,))

    def delete_entity(self, entity_id: str) -> None:
        """Delete an entity and all associated aliases and mentions (via CASCADE)."""
        self.execute("DELETE FROM entities WHERE id = ?", (entity_id,))

    # ------------------------------------------------------------------
    # Conversation helpers
    # ------------------------------------------------------------------

    def save_message(
        self,
        id: str,
        session_id: str,
        role: str,
        content: str,
        tool_call_id: str | None,
        tool_calls_json: str | None,
        created_at: str,
        turn_index: int,
    ) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO conversation_messages
                (id, session_id, role, content, tool_call_id, tool_calls_json, created_at, turn_index)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (id, session_id, role, content, tool_call_id, tool_calls_json, created_at, turn_index),
        )

    def load_session_messages(self, session_id: str) -> list[sqlite3.Row]:
        return self.execute(
            "SELECT * FROM conversation_messages WHERE session_id = ? ORDER BY turn_index",
            (session_id,),
        ).fetchall()

    def list_sessions(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.execute(
            """
            SELECT session_id, min(created_at) as started_at, count(*) as message_count
            FROM conversation_messages
            WHERE role = 'user'
            GROUP BY session_id
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    # ------------------------------------------------------------------
    # Audit helpers
    # ------------------------------------------------------------------

    def save_audit_entry(
        self,
        id: str,
        session_id: str,
        turn_index: int,
        user_query: str,
        final_response: str,
        tool_calls_json: str,
        full_message_thread_json: str,
        errors_json: str,
        latency_ms: int,
        model_id: str,
        created_at: str,
    ) -> None:
        self.execute(
            """
            INSERT INTO audit_entries
                (id, session_id, turn_index, user_query, final_response,
                 tool_calls_json, full_message_thread_json, errors_json,
                 latency_ms, model_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id, session_id, turn_index, user_query, final_response,
                tool_calls_json, full_message_thread_json, errors_json,
                latency_ms, model_id, created_at,
            ),
        )
