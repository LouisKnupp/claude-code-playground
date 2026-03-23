"""Conversation session management.

Responsibilities:
- Assign a stable session_id for the lifetime of one CLI session
- Maintain in-memory message list for the LLM context window
- Trim message history to a token budget using tiktoken
- Persist messages to SQLite for the history command
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from playground.core.models import ConversationMessage
from playground.storage.db import Database

_ENCODING_NAME = "cl100k_base"  # compatible with gpt-4 / gpt-5.x tokenizers


def _count_tokens(text: str) -> int:
    """Estimate token count. Falls back to word-split if tiktoken not installed."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding(_ENCODING_NAME)
        return len(enc.encode(text))
    except Exception:
        return len(text.split())


class ConversationSession:
    def __init__(self, db: Database, max_context_turns: int = 20) -> None:
        self._db = db
        self._max_context_turns = max_context_turns
        self.session_id: str = str(uuid.uuid4())
        self._messages: list[ConversationMessage] = []
        self._turn_counter: int = 0

    def next_turn_index(self) -> int:
        self._turn_counter += 1
        return self._turn_counter

    # ------------------------------------------------------------------
    # Adding messages
    # ------------------------------------------------------------------

    def add_user_message(self, content: str, turn_index: int) -> None:
        msg = ConversationMessage(
            id=str(uuid.uuid4()),
            session_id=self.session_id,
            role="user",
            content=content,
            created_at=datetime.utcnow(),
            turn_index=turn_index,
        )
        self._messages.append(msg)
        self._persist(msg)

    def add_assistant_message(self, content: str, turn_index: int) -> None:
        msg = ConversationMessage(
            id=str(uuid.uuid4()),
            session_id=self.session_id,
            role="assistant",
            content=content,
            created_at=datetime.utcnow(),
            turn_index=turn_index,
        )
        self._messages.append(msg)
        self._persist(msg)

    # ------------------------------------------------------------------
    # Building context for LLM
    # ------------------------------------------------------------------

    def get_context_messages(self, max_tokens: int = 8000) -> list[dict]:
        """Return the recent message history as OpenAI-format dicts, within token budget."""
        # Keep last N turns
        recent = self._messages[-self._max_context_turns * 2:]
        return self._trim_to_budget(recent, max_tokens)

    def _trim_to_budget(self, messages: list[ConversationMessage], max_tokens: int) -> list[dict]:
        """Drop oldest messages until total token count fits within budget."""
        dicts = [{"role": m.role, "content": m.content} for m in messages]
        while dicts:
            total = sum(_count_tokens(d["content"]) for d in dicts)
            if total <= max_tokens:
                break
            dicts.pop(0)  # drop oldest
        return dicts

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self, msg: ConversationMessage) -> None:
        self._db.save_message(
            id=msg.id,
            session_id=msg.session_id,
            role=msg.role,
            content=msg.content,
            tool_call_id=msg.tool_call_id,
            tool_calls_json=json.dumps(msg.tool_calls) if msg.tool_calls else None,
            created_at=msg.created_at.isoformat(),
            turn_index=msg.turn_index,
        )
        self._db.commit()
