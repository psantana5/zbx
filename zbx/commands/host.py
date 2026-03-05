"""zbx host — manage Zabbix hosts directly from the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from zbx import formatter
from zbx.config_loader import ConfigLoader
from zbx.zabbix_client import ZabbixAPIError, ZabbixClient

app = typer.Typer(help="List, create and delete Zabbix hosts.")
console = Console()

_STATUS_LABEL = {0: "[green]enabled[/green]", 1: "[red]disabled[/red]"}


# ---------------------------------------------------------------------------
# zbx host list
# ---------------------------------------------------------------------------

@app.command("list")
def host_list(
    env_file: Path = typer.Option(Path(".env"), "--env-file", "-e"),
    group: Optional[str] = typer.Option(None, "--group", "-g", help="Filter by host group name."),
    show_templates: bool = typer.Option(False, "--templates", "-t", help="Show linked templates."),
) -> None:
    """List all hosts in Zabbix."""
    loader = ConfigLoader()
    try:
        settings = loader.load_settings(env_file)
    except (FileNotFoundError, ValueError, EnvironmentError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        with ZabbixClient(settings) as client:
            hosts = client.list_hosts()
    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    if group:
        hosts = [
            h for h in hosts
            if any(g["name"].lower() == group.lower() for g in h.get("groups", []))
        ]

    if not hosts:
        console.print("[yellow]No hosts found.[/yellow]")
        return

    tbl = Table(show_header=True, header_style="bold")
    tbl.add_column("Host", style="bold")
    tbl.add_column("Display name")
    tbl.add_column("IP / Port")
    tbl.add_column("Groups")
    tbl.add_column("Status")
    if show_templates:
        tbl.add_column("Templates")

    for h in sorted(hosts, key=lambda x: x["host"].lower()):
        iface = next((i for i in h.get("interfaces", []) if i.get("main") == "1"), None)
        ip_port = f"{iface['ip']}:{iface['port']}" if iface else "—"
        groups_str = ", ".join(g["name"] for g in h.get("groups", []))
        status_str = _STATUS_LABEL.get(int(h.get("status", 0)), "?")
        row = [h["host"], h.get("name", ""), ip_port, groups_str, status_str]
        if show_templates:
            tmpls = ", ".join(t.get("host", t.get("name", "")) for t in h.get("parentTemplates", []))
            row.append(tmpls or "—")
        tbl.add_row(*row)

    console.print(tbl)
    console.print(f"\n[dim]{len(hosts)} host(s)[/dim]")


# ---------------------------------------------------------------------------
# zbx host create
# ---------------------------------------------------------------------------

@app.command("create")
def host_create(
    host: str = typer.Argument(..., help="Technical host name (unique identifier in Zabbix)."),
    ip: str = typer.Option(..., "--ip", help="IP address of the monitored host."),
    group: str = typer.Option(..., "--group", "-g", help="Host group name (must exist in Zabbix)."),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Display name (defaults to host)."),
    port: int = typer.Option(10050, "--port", "-p", help="Zabbix agent port."),
    template: Optional[list[str]] = typer.Option(  # noqa: UP007
        None, "--template", "-T", help="Template name to link (repeat for multiple)."
    ),
    description: str = typer.Option("", "--description", "-d", help="Optional description."),
    env_file: Path = typer.Option(Path(".env"), "--env-file", "-e"),
) -> None:
    """Create a new host in Zabbix."""
    display_name = name or host
    loader = ConfigLoader()
    try:
        settings = loader.load_settings(env_file)
    except (FileNotFoundError, ValueError, EnvironmentError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        with ZabbixClient(settings) as client:
            # Resolve group
            hg = client.get_hostgroup(group)
            if not hg:
                formatter.print_error(f"Host group '{group}' not found in Zabbix.")
                raise typer.Exit(1)

            # Resolve templates
            template_ids: list[str] = []
            for tmpl_name in (template or []):
                t = client.get_template(tmpl_name)
                if not t:
                    formatter.print_error(f"Template '{tmpl_name}' not found in Zabbix.")
                    raise typer.Exit(1)
                template_ids.append(t["templateid"])

            # Check for existing host
            existing = client.get_host(host)
            if existing:
                formatter.print_error(f"Host '{host}' already exists (hostid={existing['hostid']}).")
                raise typer.Exit(1)

            hostid = client.create_host(
                host=host,
                name=display_name,
                ip=ip,
                port=port,
                group_ids=[hg["groupid"]],
                description=description,
                template_ids=template_ids if template_ids else None,
            )

    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]✓[/green] Created host [bold]{host}[/bold] (hostid={hostid})")
    if template_ids:
        console.print(f"  Linked {len(template_ids)} template(s): {', '.join(template or [])}")


# ---------------------------------------------------------------------------
# zbx host delete
# ---------------------------------------------------------------------------

@app.command("delete")
def host_delete(
    host: str = typer.Argument(..., help="Technical host name to delete."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
    env_file: Path = typer.Option(Path(".env"), "--env-file", "-e"),
) -> None:
    """Delete a host from Zabbix (irreversible)."""
    loader = ConfigLoader()
    try:
        settings = loader.load_settings(env_file)
    except (FileNotFoundError, ValueError, EnvironmentError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        with ZabbixClient(settings) as client:
            existing = client.get_host(host)
            if not existing:
                formatter.print_error(f"Host '{host}' not found in Zabbix.")
                raise typer.Exit(1)

            hostid = existing["hostid"]
            linked = existing.get("parentTemplates", [])

            if linked:
                names = ", ".join(t.get("host", "?") for t in linked)
                console.print(f"[yellow]Warning:[/yellow] Host has {len(linked)} linked template(s): {names}")

            if not force:
                confirmed = typer.confirm(
                    f"Delete host '{host}' (hostid={hostid})? This cannot be undone.",
                    default=False,
                )
                if not confirmed:
                    console.print("[yellow]Aborted.[/yellow]")
                    raise typer.Exit(0)

            client.delete_host(hostid)

    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]✓[/green] Deleted host [bold]{host}[/bold]")
