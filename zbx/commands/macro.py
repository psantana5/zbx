"""zbx macro — manage Zabbix global macros from the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from zbx import formatter
from zbx.config_loader import ConfigLoader
from zbx.models import HostMacro
from zbx.zabbix_client import ZabbixAPIError, ZabbixClient

app = typer.Typer(help="List, set and delete global Zabbix macros.")
console = Console()


# ---------------------------------------------------------------------------
# zbx macro list
# ---------------------------------------------------------------------------

@app.command("list")
def macro_list(
    env_file: Path = typer.Option(Path(".env"), "--env-file", "-e"),
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Filter by macro name (partial match)."),
) -> None:
    """List all global macros in Zabbix."""
    loader = ConfigLoader()
    try:
        settings = loader.load_settings(env_file)
    except (FileNotFoundError, ValueError, EnvironmentError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        with ZabbixClient(settings) as client:
            macros = client.list_global_macros()
    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    if search:
        macros = [m for m in macros if search.lower() in m["macro"].lower()]

    if not macros:
        console.print("[yellow]No global macros found.[/yellow]")
        return

    tbl = Table(show_header=True, header_style="bold")
    tbl.add_column("Macro", style="bold cyan")
    tbl.add_column("Value")
    tbl.add_column("Description", style="dim")

    for m in sorted(macros, key=lambda x: x["macro"]):
        tbl.add_row(m["macro"], m.get("value", ""), m.get("description", ""))

    console.print(tbl)
    console.print(f"\n[dim]{len(macros)} global macro(s)[/dim]")


# ---------------------------------------------------------------------------
# zbx macro set
# ---------------------------------------------------------------------------

@app.command("set")
def macro_set(
    macro: str = typer.Argument(..., help="Macro name, e.g. {$SLACK_WEBHOOK}."),
    value: str = typer.Argument(..., help="Value to set."),
    description: str = typer.Option("", "--description", "-d", help="Optional description."),
    env_file: Path = typer.Option(Path(".env"), "--env-file", "-e"),
) -> None:
    """Create or update a global macro (upsert)."""
    # Validate macro format
    try:
        HostMacro(macro=macro, value=value)
    except Exception as exc:
        formatter.print_error(f"Invalid macro name '{macro}': {exc}")
        raise typer.Exit(1) from exc

    loader = ConfigLoader()
    try:
        settings = loader.load_settings(env_file)
    except (FileNotFoundError, ValueError, EnvironmentError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        with ZabbixClient(settings) as client:
            existing = client.get_global_macro(macro)
            if existing:
                client.update_global_macro(existing["globalmacroid"], value, description)
                console.print(f"[yellow]~[/yellow] Updated global macro [bold cyan]{macro}[/bold cyan] = {value!r}")
            else:
                client.create_global_macro(macro, value, description)
                console.print(f"[green]+[/green] Created global macro [bold cyan]{macro}[/bold cyan] = {value!r}")
    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc


# ---------------------------------------------------------------------------
# zbx macro delete
# ---------------------------------------------------------------------------

@app.command("delete")
def macro_delete(
    macro: str = typer.Argument(..., help="Macro name to delete, e.g. {$SLACK_WEBHOOK}."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
    env_file: Path = typer.Option(Path(".env"), "--env-file", "-e"),
) -> None:
    """Delete a global macro from Zabbix."""
    loader = ConfigLoader()
    try:
        settings = loader.load_settings(env_file)
    except (FileNotFoundError, ValueError, EnvironmentError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        with ZabbixClient(settings) as client:
            existing = client.get_global_macro(macro)
            if not existing:
                formatter.print_error(f"Global macro '{macro}' not found.")
                raise typer.Exit(1)

            if not force:
                confirmed = typer.confirm(
                    f"Delete global macro '{macro}' (value={existing.get('value', '')!r})?",
                    default=False,
                )
                if not confirmed:
                    console.print("[yellow]Aborted.[/yellow]")
                    raise typer.Exit(0)

            client.delete_global_macro(existing["globalmacroid"])
    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]✓[/green] Deleted global macro [bold cyan]{macro}[/bold cyan]")
