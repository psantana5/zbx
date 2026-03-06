"""zbx status — show connection info and Zabbix server stats."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table

from zbx import __version__
from zbx.config_loader import ConfigLoader
from zbx.zabbix_client import ZabbixAPIError, ZabbixClient

console = Console()


def _build_table(settings, client) -> Table:
    """Fetch stats and return a Rich Table."""
    api_version = client.version_str
    template_count = len(client.get_all_templates())
    hosts = client.list_hosts()
    host_count = len(hosts)
    enabled_count = sum(1 for h in hosts if h.get("status") == "0")

    # Recent problems — triggers in PROBLEM state
    try:
        problems = client.call(
            "problem.get",
            {"output": ["objectid", "name", "severity"], "limit": 5, "recent": True},
        )
    except Exception:  # noqa: BLE001
        problems = []

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("key", style="dim")
    table.add_column("value", style="bold")

    table.add_row("zbx version", __version__)
    table.add_row("Zabbix server", settings.url)
    table.add_row("API version", api_version)
    table.add_row("Authenticated as", settings.username)
    table.add_row("Templates", str(template_count))
    table.add_row("Hosts", f"{host_count} total  ({enabled_count} enabled)")
    table.add_row("Active problems", str(len(problems)))

    if problems:
        table.add_row("", "")
        table.add_row("[dim]Recent problems[/dim]", "")
        sev_map = {"0": "not classified", "1": "info", "2": "warning",
                   "3": "average", "4": "high", "5": "disaster"}
        for p in problems[:5]:
            sev = sev_map.get(p.get("severity", "0"), "?")
            table.add_row(f"  [red]•[/red] {sev}", p.get("name", "—"))

    return table


def status_cmd(
    env_file: Annotated[Path, typer.Option("--env-file", "-e", help="Path to .env file.")] = Path(".env"),
    watch: Annotated[bool, typer.Option("--watch", "-w", help="Refresh continuously.")] = False,
    interval: Annotated[int, typer.Option("--interval", "-i", help="Refresh interval in seconds (--watch only).")] = 5,
) -> None:
    """Show connection status, Zabbix version and resource counts.

    Use --watch / -w for a live-refreshing dashboard:

      zbx status --watch
      zbx status --watch --interval 10
    """
    loader = ConfigLoader()
    try:
        settings = loader.load_settings(env_file)
    except EnvironmentError as exc:
        console.print(f"[red]fail[/red] {exc}")
        raise typer.Exit(1) from exc

    def _fetch_and_render() -> Optional[Table]:
        try:
            with ZabbixClient(settings) as client:
                return _build_table(settings, client)
        except ZabbixAPIError as exc:
            console.print(f"[red]fail[/red] Zabbix API error: {exc}")
            return None
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]fail[/red] Cannot connect: {exc}")
            return None

    if not watch:
        table = _fetch_and_render()
        if table is None:
            raise typer.Exit(1)
        console.print()
        console.print(table)
        console.print()
        console.print("[green]✓[/green]  Connected and authenticated.\n")
        return

    # ── Live watch mode ───────────────────────────────────────────────────
    console.print(f"[dim]Watching {settings.url} — refresh every {interval}s  (Ctrl+C to exit)[/dim]\n")
    try:
        with Live(console=console, refresh_per_second=1) as live:
            while True:
                table = _fetch_and_render()
                if table:
                    live.update(table)
                time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")
