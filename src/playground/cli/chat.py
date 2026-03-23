"""Interactive chat REPL."""

from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

console = Console()

_COMMANDS = {
    "/quit": "Exit the chat",
    "/exit": "Exit the chat",
    "/help": "Show available commands",
    "/history": "Show recent sessions (use 'playground history' instead)",
}


def run_chat(session, provider, audit_logger, settings) -> None:
    """Start the interactive chat loop."""
    from playground.pipeline import agent_loop

    console.print(Panel(
        "[bold]Playground[/bold] — your work context assistant\n"
        "[dim]Type your question. /help for commands. Ctrl-C or /quit to exit.[/dim]",
        border_style="blue",
    ))

    while True:
        try:
            user_input = console.input("[bold blue]You:[/bold blue] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            if _handle_command(user_input):
                break
            continue

        # Run the agent loop
        with console.status("[dim]Thinking…[/dim]", spinner="dots"):
            try:
                response = agent_loop.run(
                    user_query=user_input,
                    session=session,
                    provider=provider,
                    audit_logger=audit_logger,
                    max_iterations=settings.max_agent_iterations,
                )
            except Exception as exc:
                console.print(f"[red]Error: {exc}[/red]")
                continue

        # Print the response
        console.print()
        console.print(Rule(style="dim"))
        console.print(Markdown(response.content))

        # Print sources
        if response.sources:
            console.print()
            console.print("[dim]Sources:[/dim]")
            for src in response.sources:
                label = f"{src.source_type.replace('_', ' ').title()} — {src.title}"
                console.print(f"  [dim]•[/dim] [cyan]{label}[/cyan]")
                if src.excerpt:
                    console.print(f"    [italic dim]\"{src.excerpt.strip()}\"[/italic dim]")
                console.print(f"    [dim]{src.deep_link}[/dim]")

        console.print(Rule(style="dim"))
        console.print()


def _handle_command(cmd: str) -> bool:
    """Handle slash commands. Returns True if the loop should exit."""
    cmd = cmd.lower().strip()
    if cmd in ("/quit", "/exit"):
        console.print("[dim]Goodbye.[/dim]")
        return True
    if cmd == "/help":
        for command, desc in _COMMANDS.items():
            console.print(f"  [cyan]{command}[/cyan]  {desc}")
    else:
        console.print(f"[yellow]Unknown command: {cmd}[/yellow]")
    return False
