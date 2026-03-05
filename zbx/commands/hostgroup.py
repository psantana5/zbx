"""zbx hostgroup — manage Zabbix host groups from the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from zbx import formatter
from zbx.config_loader import ConfigLoader
from zbx.zabbix_client import ZabbixAPIError, ZabbixClient

app = typer.Typer(help="List, create and delete Zabbix host groups.")
console = Console()


# ---------------------------------------------------------------------------
# zbx hostgroup list
# ---------------------------------------------------------------------------

@app.command("list")
def hostgroup_list(
    env_file: Path = typer.Option(Path(".env"), "--env-file", "-e"),
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Filter by name (partial match)."),
    show_hosts: bool = typer.Option(False, "--hosts", help="Show host count per group."),
) -> None:
    """List all host groups in Zabbix."""
    loader = ConfigLoader()
    try:
        settings = loader.load_settings(env_file)
    except (FileNotFoundError, ValueError, EnvironmentError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        with ZabbixClient(settings) as client:
            groups = client.list_hostgroups()
            if show_hosts:
                hosts = client.list_hosts()
    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    if search:
        groups = [g for g in groups if search.lower() in g["name"].lower()]

    if not groups:
        console.print("[yellow]No host groups found.[/yellow]")
        return

    # Build host count map if requested
    host_count: dict[str, int] = {}
    if show_hosts:
        for h in hosts:
            for g in h.get("groups", []):
                gid = g["groupid"]
                host_count[gid] = host_count.get(gid, 0) + 1

    tbl = Table(show_header=True, header_style="bold")
    tbl.add_column("ID", style="dim")
    tbl.add_column("Group name", style="bold")
    if show_hosts:
        tbl.add_column("Hosts", justify="right")

    for g in sorted(groups, key=lambda x: x["name"].lower()):
        row = [g["groupid"], g["name"]]
        if show_hosts:
            row.append(str(host_count.get(g["groupid"], 0)))
        tbl.add_row(*row)

    console.print(tbl)
    console.print(f"\n[dim]{len(groups)} group(s)[/dim]")


# ---------------------------------------------------------------------------
# zbx hostgroup create
# ---------------------------------------------------------------------------

@app.command("create")
def hostgroup_create(
    name: str = typer.Argument(..., help="Host group name to create."),
    env_file: Path = typer.Option(Path(".env"), "--env-file", "-e"),
) -> None:
    """Create a new host group in Zabbix."""
    loader = ConfigLoader()
    try:
        settings = loader.load_settings(env_file)
    except (FileNotFoundError, ValueError, EnvironmentError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        with ZabbixClient(settings) as client:
            existing = client.get_hostgroup(name)
            if existing:
                formatter.print_error(f"Host group '{name}' already exists (id={existing['groupid']}).")
                raise typer.Exit(1)
            groupid = client.ensure_hostgroup(name)
    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]✓[/green] Created host group [bold]{name}[/bold] (id={groupid})")


# ---------------------------------------------------------------------------
# zbx hostgroup delete
# ---------------------------------------------------------------------------

@app.command("delete")
def hostgroup_delete(
    name: str = typer.Argument(..., help="Host group name to delete."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
    env_file: Path = typer.Option(Path(".env"), "--env-file", "-e"),
) -> None:
    """Delete a host group from Zabbix. Fails if the group still has hosts."""
    loader = ConfigLoader()
    try:
        settings = loader.load_settings(env_file)
    except (FileNotFoundError, ValueError, EnvironmentError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        with ZabbixClient(settings) as client:
            existing = client.get_hostgroup(name)
            if not existing:
                formatter.print_error(f"Host group '{name}' not found.")
                raise typer.Exit(1)

            groupid = existing["groupid"]

            # Check for hosts in this group
            all_hosts = client.list_hosts()
            members = [
                h["host"] for h in all_hosts
                if any(g["groupid"] == groupid for g in h.get("groups", []))
            ]
            if members:
                formatter.print_error(
                    f"Host group '{name}' still has {len(members)} host(s): "
                    + ", ".join(members[:5])
                    + (" …" if len(members) > 5 else "")
                    + ". Remove or reassign them first."
                )
                raise typer.Exit(1)

            if not force:
                confirmed = typer.confirm(
                    f"Delete empty host group '{name}' (id={groupid})?",
                    default=False,
                )
                if not confirmed:
                    console.print("[yellow]Aborted.[/yellow]")
                    raise typer.Exit(0)

            client.delete_hostgroup(groupid)
    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]✓[/green] Deleted host group [bold]{name}[/bold]")
