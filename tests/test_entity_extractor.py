import json
from datetime import datetime

from playground.core.models import Document
from playground.pipeline.entity_extractor import extract_and_store
from playground.providers.base import LLMResponse
from playground.storage.db import Database


class _FakeProvider:
    model_id = "fake-model"

    def __init__(self, payload: list[dict]):
        self._payload = payload

    def complete(self, messages: list[dict]) -> LLMResponse:
        return LLMResponse(content=json.dumps(self._payload), finish_reason="stop")


def test_extract_and_store_reuses_existing_entity_without_alias(tmp_path):
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

    provider = _FakeProvider([
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
    )

    stored = extract_and_store(doc, provider, db)

    assert stored == 1

    aliases = db.get_aliases("entity-1")
    assert "Anthony / Louis" in aliases
    assert "Anthony and Louis" in aliases

    mentions = db.get_mentions_for_entity("entity-1")
    assert len(mentions) == 1
