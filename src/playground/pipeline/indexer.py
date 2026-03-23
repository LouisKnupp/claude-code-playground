"""Ingestion orchestrator.

fetch → hash dedup → FTS5 index → entity extraction

The dedup check happens before entity extraction (the expensive LLM call),
so unchanged documents are skipped entirely.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from playground.connectors.base import DataConnector
from playground.core.models import Document
from playground.pipeline.entity_extractor import extract_and_store
from playground.providers.base import LLMProvider
from playground.storage.db import Database


@dataclass
class IndexResult:
    connector_name: str
    total_fetched: int = 0
    indexed: int = 0
    skipped: int = 0
    entities_extracted: int = 0
    errors: list[str] = field(default_factory=list)


def index_connector(
    connector: DataConnector,
    db: Database,
    provider: LLMProvider,
    since: datetime | None = None,
    extract_entities: bool = True,
) -> IndexResult:
    """Run the full ingestion pipeline for one connector."""
    result = IndexResult(connector_name=connector.display_name)

    # 1. Fetch documents
    try:
        if since:
            docs = connector.fetch_updated(since)
        else:
            docs = connector.fetch_all()
    except Exception as exc:
        result.errors.append(f"Fetch failed: {exc}")
        return result

    result.total_fetched = len(docs)

    # 2. Load existing hashes for dedup
    existing_hashes = db.get_existing_hashes(connector.source_type)

    for doc in docs:
        # 3. Skip unchanged documents
        if existing_hashes.get(doc.id) == doc.content_hash:
            result.skipped += 1
            continue

        # 4. Upsert document into SQLite (triggers FTS5 sync automatically)
        try:
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
            result.indexed += 1
        except Exception as exc:
            result.errors.append(f"Failed to index '{doc.title}': {exc}")
            continue

        # 5. Entity extraction (LLM call — only for new/changed docs)
        if extract_entities:
            try:
                count = extract_and_store(doc, provider, db)
                result.entities_extracted += count
            except Exception as exc:
                result.errors.append(f"Entity extraction failed for '{doc.title}': {exc}")

    return result
