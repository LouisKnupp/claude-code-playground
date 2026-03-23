"""CLI entry point.

Importing this module triggers self-registration of all providers, connectors,
and tools via their module-level register() calls.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import parse_qs, urlparse

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
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show per-file progress and errors in real time.")] = False,
):
    """Index documents from all enabled connectors."""
    from playground.cli.sync import run_sync

    settings, db, provider = _bootstrap()
    run_sync(settings=settings, db=db, provider=provider, watch=watch, poll_seconds=poll, verbose=verbose)


@app.command(name="cleanup-entities")
def cleanup_entities(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview changes without writing.")] = False,
    delete_unresolvable: Annotated[bool, typer.Option("--delete-unresolvable", help="Delete entities that cannot be resolved to a full name.")] = False,
):
    """Fix first-name-only person entities by resolving them to full names."""
    from playground.core.roster import EmployeeRoster
    from playground.pipeline.entity_cleanup import run_cleanup
    from rich.table import Table

    settings, db, _ = _bootstrap()

    roster = EmployeeRoster.from_file(settings.employees_file, settings.name_overrides_file)
    if roster.all_names:
        console.print(f"[dim]Loaded {len(roster.all_names)} employees from roster.[/dim]")

    label = "[yellow]DRY RUN[/yellow] — " if dry_run else ""
    console.print(f"\n{label}Scanning for ambiguous person entities…\n")

    result = run_cleanup(db, dry_run=dry_run, roster=roster, delete_unresolvable=delete_unresolvable)

    if result.promoted:
        t = Table("First Name", "→ Full Name", title="Promoted (renamed)", style="green")
        for old, new in result.promoted:
            t.add_row(old, new)
        console.print(t)

    if result.merged:
        t = Table("First Name", "→ Merged Into", title="Merged into existing entity", style="blue")
        for old, kept in result.merged:
            t.add_row(old, kept)
        console.print(t)

    if result.ambiguous:
        t = Table("First Name", "Candidates (ambiguous)", title="Ambiguous — needs manual review", style="yellow")
        for name, candidates in result.ambiguous:
            t.add_row(name, ", ".join(candidates))
        console.print(t)
        console.print("[dim]Tip: re-run sync after fixing these manually or adding more transcript data.[/dim]")

    if result.unresolvable:
        t = Table("First Name", title="Unresolvable — no speaker match found", style="red")
        for name in result.unresolvable:
            t.add_row(name)
        console.print(t)

    total = len(result.promoted) + len(result.merged)
    suffix = " (no writes)" if dry_run else ""
    console.print(
        f"\n[bold]Done.[/bold] {total} fixed, "
        f"{len(result.ambiguous)} ambiguous, "
        f"{len(result.unresolvable)} unresolvable{suffix}."
    )


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


@app.command("zoom-auth")
def zoom_auth(
    code: Annotated[str | None, typer.Option("--code", help="Authorization code returned by Zoom.")] = None,
):
    """Start or complete the Zoom General App OAuth flow for cloud recordings."""
    from playground.connectors.zoom import ZoomCloudClient
    from playground.core.config import load_settings, save_config_values

    settings = load_settings()
    client = ZoomCloudClient(
        client_id=settings.zoom_api_client_id,
        client_secret=settings.zoom_api_client_secret,
        redirect_uri=settings.zoom_api_redirect_uri,
        user_id=settings.zoom_api_user_id,
        access_token=settings.zoom_api_access_token,
        refresh_token=settings.zoom_api_refresh_token,
        token_expires_at=settings.zoom_api_token_expires_at,
        token_updater=lambda tokens: save_config_values(tokens, settings.config_path),
    )

    if not code:
        console.print("[bold]Zoom OAuth Authorization URL[/bold]")
        console.print(client.build_authorize_url())
        console.print()
        console.print(
            "[dim]Authorize the app in your browser, then copy the 'code' query parameter "
            "from the redirect URL and run:[/dim]"
        )
        console.print("[cyan]playground zoom-auth --code <your-code>[/cyan]")
        return

    auth_code = code.strip()
    if auth_code.startswith("http://") or auth_code.startswith("https://"):
        parsed = urlparse(auth_code)
        auth_code = parse_qs(parsed.query).get("code", [""])[0]
        if not auth_code:
            console.print("[red]Error:[/red] No 'code' query parameter found in the provided URL.")
            raise typer.Exit(1)

    tokens = client.exchange_code(auth_code)
    save_config_values(
        {
            "zoom_api_access_token": tokens["access_token"],
            "zoom_api_refresh_token": tokens["refresh_token"],
            "zoom_api_token_expires_at": tokens["token_expires_at"],
        },
        settings.config_path,
    )
    console.print("[green]Zoom OAuth tokens saved.[/green]")
    console.print(f"[dim]{settings.config_path}[/dim]")


if __name__ == "__main__":
    app()
