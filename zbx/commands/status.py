"""zbx status — show connection info and Zabbix server stats."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from zbx import __version__
from zbx.config_loader import ConfigLoader
from zbx.zabbix_client import ZabbixAPIError, ZabbixClient

console = Console()


def status_cmd(
    env_file: Path = typer.Option(
        Path(".env"), "--env-file", "-e", help="Path to .env file."
    ),
) -> None:
    """Show connection status, Zabbix version and resource counts."""
    loader = ConfigLoader()
    try:
        settings = loader.load_settings(env_file)
    except EnvironmentError as exc:
        console.print(f"[red]fail[/red] {exc}")
        raise typer.Exit(1) from exc

    try:
        with ZabbixClient(settings) as client:
            api_version = client.version_str
            template_count = len(client.get_all_templates())
            hosts = client.list_hosts()
            host_count = len(hosts)
            enabled_count = sum(1 for h in hosts if h.get("status") == "0")
    except ZabbixAPIError as exc:
        console.print(f"[red]fail[/red] Zabbix API error: {exc}")
        raise typer.Exit(1) from exc
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]fail[/red] Cannot connect: {exc}")
        raise typer.Exit(1) from exc

    console.print()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("key", style="dim")
    table.add_column("value", style="bold")

    table.add_row("zbx version", __version__)
    table.add_row("Zabbix server", settings.url)
    table.add_row("API version", api_version)
    table.add_row("Authenticated as", settings.username)
    table.add_row("Templates", str(template_count))
    table.add_row("Hosts", f"{host_count} total  ({enabled_count} enabled)")

    console.print(table)
    console.print()
    console.print("[green]✓[/green]  Connected and authenticated.\n")
