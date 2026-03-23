"""Audit logger — persists every AuditEntry to SQLite."""

from __future__ import annotations

from playground.core.models import AuditEntry
from playground.storage.db import Database


class AuditLogger:
    def __init__(self, db: Database) -> None:
        self._db = db

    def log(self, entry: AuditEntry) -> None:
        self._db.save_audit_entry(
            id=entry.id,
            session_id=entry.session_id,
            turn_index=entry.turn_index,
            user_query=entry.user_query,
            final_response=entry.final_response,
            tool_calls_json=entry.tool_calls_json(),
            full_message_thread_json=entry.full_thread_json(),
            errors_json=entry.errors_json(),
            latency_ms=entry.latency_ms,
            model_id=entry.model_id,
            created_at=entry.created_at.isoformat(),
        )
        self._db.commit()
