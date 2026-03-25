"""Ingestion orchestrator.

fetch → hash dedup → FTS5 index → entity extraction

The dedup check happens before entity extraction (the expensive LLM call),
so unchanged documents are skipped entirely.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from playground.connectors.base import DataConnector
from playground.core.models import Document
from playground.core.roster import EmployeeRoster
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
    verbose: bool = False,
    console: Console | None = None,
    roster: EmployeeRoster | None = None,
) -> IndexResult:
    """Run the full ingestion pipeline for one connector."""
    if console is None:
        console = Console()

    result = IndexResult(connector_name=connector.display_name)

    # 1. Fetch documents
    if verbose:
        console.print("  [dim]→ Fetching document list…[/dim]")
    try:
        if since:
            docs = connector.fetch_updated(since)
        else:
            docs = connector.fetch_all()
    except Exception as exc:
        msg = f"Fetch failed: {exc}"
        result.errors.append(msg)
        console.print(f"  [red]✗ {msg}[/red]")
        return result

    result.total_fetched = len(docs)

    if verbose:
        console.print(f"  [dim]→ Found {len(docs)} file(s)[/dim]")

    if not docs:
        return result

    # 2. Load existing hashes for dedup
    existing_hashes = db.get_existing_hashes(connector.source_type)

    # 3. Process documents with a progress bar
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[cyan]{task.completed}[/cyan]/[cyan]{task.total}[/cyan]"),
        TimeElapsedColumn(),
        console=console,
        transient=not verbose,
    ) as progress:
        task = progress.add_task("[dim]Starting…[/dim]", total=len(docs))

        for doc in docs:
            short_name = doc.title[:55] + "…" if len(doc.title) > 55 else doc.title
            progress.update(task, description=f"[dim]{short_name}[/dim]")

            # --- Dedup check ---
            if existing_hashes.get(doc.id) == doc.content_hash:
                result.skipped += 1
                if verbose:
                    console.print(f"  [dim]  skip  {doc.title}[/dim]")
                progress.advance(task)
                continue

            # --- Index to SQLite / FTS5 ---
            if verbose:
                console.print(f"  [green]  index[/green] {doc.title}")
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
                # Remove any stale old-format documents for the same cloud recording
                # (produced before doc_id was stabilised to sha256(source_id)).
                recording_file_id = doc.metadata.get("recording_file_id", "")
                if doc.source_type == "zoom" and recording_file_id:
                    removed = db.delete_stale_cloud_docs(recording_file_id, doc.id)
                    if verbose and removed:
                        console.print(
                            f"  [dim]  cleaned {removed} stale doc(s) for recording {recording_file_id[:8]}…[/dim]"
                        )
                db.commit()
                result.indexed += 1
            except Exception as exc:
                msg = f"Failed to index '{doc.title}': {exc}"
                result.errors.append(msg)
                console.print(f"  [red]  ✗ {msg}[/red]")
                progress.advance(task)
                continue

            # --- Entity extraction (LLM call — only for new/changed docs) ---
            if extract_entities:
                if verbose:
                    console.print(f"  [blue]  extract entities[/blue] {doc.title}")
                t0 = time.monotonic()
                try:
                    count = extract_and_store(doc, provider, db, roster=roster)
                    result.entities_extracted += count
                    if verbose:
                        elapsed = time.monotonic() - t0
                        console.print(
                            f"    [dim]↳ {count} entit{'y' if count == 1 else 'ies'} "
                            f"in {elapsed:.1f}s[/dim]"
                        )
                except Exception as exc:
                    msg = f"Entity extraction failed for '{doc.title}': {exc}"
                    result.errors.append(msg)
                    console.print(f"  [red]  ✗ {msg}[/red]")
                finally:
                    progress.advance(task)
            else:
                progress.advance(task)

    return result
