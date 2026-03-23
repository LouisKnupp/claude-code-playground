"""CLI entry point.

Importing this module triggers self-registration of all providers, connectors,
and tools via their module-level register() calls.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="playground",
    help="AI work context assistant — search your Zoom calls and notes.",
    no_args_is_help=True,
)
console = Console()


def _bootstrap():
    """Load settings, open DB, register providers/tools, return (settings, db, provider)."""
    from playground.core.config import load_settings
    from playground.storage.db import Database

    # Provider registration
    import playground.providers.openai  # noqa: F401 — triggers registry.register("openai", ...)

    # Tool registration + DB injection
    import playground.tools.search_zoom  # noqa: F401
    import playground.tools.search_notes  # noqa: F401
    import playground.tools.lookup_person  # noqa: F401
    from playground.tools import search_zoom, search_notes, lookup_person
    from playground.providers import registry as prov_registry

    settings = load_settings()

    if not settings.openai_api_key:
        console.print("[red]Error:[/red] OPENAI_API_KEY is not set.")
        console.print("Set it in your environment or in ~/.playground/config.toml")
        raise typer.Exit(1)

    db = Database(settings.db_path)

    # Inject DB into tools that need it
    search_zoom.init(db)
    search_notes.init(db)
    lookup_person.init(db)

    provider = prov_registry.get(
        settings.llm_provider,
        model=settings.llm_model,
        api_key=settings.openai_api_key,
    )

    return settings, db, provider


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def chat():
    """Start an interactive chat session with your work context."""
    from playground.core.audit import AuditLogger
    from playground.core.session import ConversationSession
    from playground.cli.chat import run_chat

    settings, db, provider = _bootstrap()
    session = ConversationSession(db=db, max_context_turns=settings.max_context_turns)
    audit_logger = AuditLogger(db=db)

    run_chat(session=session, provider=provider, audit_logger=audit_logger, settings=settings)


@app.command()
def sync(
    watch: Annotated[bool, typer.Option("--watch", help="Watch for new files after initial sync.")] = False,
    poll: Annotated[int, typer.Option(help="Watch poll interval in seconds.")] = 30,
):
    """Index documents from all enabled connectors."""
    from playground.cli.sync import run_sync

    settings, db, provider = _bootstrap()
    run_sync(settings=settings, db=db, provider=provider, watch=watch, poll_seconds=poll)


@app.command()
def history(
    limit: Annotated[int, typer.Option(help="Number of past sessions to show.")] = 10,
):
    """Show recent conversation sessions."""
    settings, db, _ = _bootstrap()

    sessions = db.list_sessions(limit=limit)
    if not sessions:
        console.print("[dim]No conversation history yet.[/dim]")
        return

    table = Table("Session ID", "Started", "Messages", title="Recent Sessions")
    for row in sessions:
        table.add_row(row["session_id"][:8] + "…", row["started_at"], str(row["message_count"]))
    console.print(table)


if __name__ == "__main__":
    app()
