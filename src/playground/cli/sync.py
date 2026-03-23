"""sync command — index all enabled connectors, with optional --watch mode."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

console = Console()


def run_sync(
    settings,
    db,
    provider,
    watch: bool = False,
    poll_seconds: int = 30,
) -> None:
    """Index all enabled connectors. If watch=True, poll for changes afterwards."""
    from playground.connectors import registry as conn_registry
    from playground.pipeline.indexer import index_connector

    # Import connector modules to trigger self-registration
    for name in settings.enabled_connectors:
        _import_connector(name)

    def _do_sync(since: datetime | None = None) -> None:
        for name in settings.enabled_connectors:
            try:
                connector = _build_connector(name, settings)
            except Exception as exc:
                console.print(f"[yellow]⚠ Could not load connector '{name}': {exc}[/yellow]")
                continue

            label = f"Syncing [bold]{connector.display_name}[/bold]..."
            with console.status(label):
                result = index_connector(
                    connector=connector,
                    db=db,
                    provider=provider,
                    since=since,
                )

            _print_result(result)

    console.print("[bold]Starting sync…[/bold]")
    _do_sync(since=None)

    if watch:
        console.print(f"\n[dim]Watching for changes every {poll_seconds}s. Ctrl-C to stop.[/dim]")
        last_sync = datetime.utcnow()
        try:
            while True:
                time.sleep(poll_seconds)
                since = last_sync - timedelta(seconds=5)  # small overlap to avoid gaps
                last_sync = datetime.utcnow()
                _do_sync(since=since)
        except KeyboardInterrupt:
            console.print("\n[dim]Watch stopped.[/dim]")


def _print_result(result) -> None:
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_row(result.connector_name)
    table.add_row("  Fetched", str(result.total_fetched))
    table.add_row("  Indexed", f"[green]{result.indexed}[/green]")
    table.add_row("  Skipped (unchanged)", str(result.skipped))
    table.add_row("  Entities extracted", str(result.entities_extracted))
    if result.errors:
        for err in result.errors:
            table.add_row("  [red]Error[/red]", err)
    console.print(table)


def _import_connector(name: str) -> None:
    if name == "zoom":
        import playground.connectors.zoom  # noqa: F401
    elif name == "apple_notes":
        import playground.connectors.apple_notes  # noqa: F401


def _build_connector(name: str, settings):
    from playground.connectors import registry as conn_registry

    if name == "zoom":
        return conn_registry.get("zoom", transcripts_dir=settings.zoom_transcripts_dir)
    elif name == "apple_notes":
        return conn_registry.get("apple_notes")
    else:
        return conn_registry.get(name)
