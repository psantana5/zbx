"""zbx inventory — manage host inventory (Ansible-style)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from zbx import formatter
from zbx.config_loader import ConfigLoader
from zbx.models import Inventory, InventoryHost
from zbx.zabbix_client import ZabbixAPIError, ZabbixClient

console = Console()
app = typer.Typer(help="Manage host inventory (list, apply).")


# ---------------------------------------------------------------------------
# zbx inventory list
# ---------------------------------------------------------------------------

@app.command("list")
def inventory_list(
    env_file: Path = typer.Option(
        Path(".env"), "--env-file", "-e", help="Path to .env file."
    ),
    group: Optional[str] = typer.Option(
        None, "--group", "-g", help="Filter by host group name."
    ),
) -> None:
    """List all hosts currently in Zabbix — use this to find exact host names."""
    loader = ConfigLoader()
    try:
        settings = loader.load_settings(env_file)
    except EnvironmentError as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        with ZabbixClient(settings) as client:
            hosts = client.list_hosts()
    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    if group:
        hosts = [h for h in hosts if any(g["name"] == group for g in h.get("groups", []))]

    if not hosts:
        console.print("[yellow]No hosts found.[/yellow]")
        raise typer.Exit(0)

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Host (technical name)", style="bold")
    table.add_column("Display name")
    table.add_column("IP", style="dim")
    table.add_column("Port", style="dim", justify="right")
    table.add_column("Groups")
    table.add_column("Templates", style="dim")
    table.add_column("Status")

    for h in sorted(hosts, key=lambda x: x["host"]):
        ip = port = "—"
        for iface in h.get("interfaces", []):
            if iface.get("main") == "1":
                ip = iface.get("ip", "—")
                port = iface.get("port", "—")
                break

        groups = ", ".join(g["name"] for g in h.get("groups", []))
        templates = ", ".join(t["host"] for t in h.get("parentTemplates", []))
        status_text = Text("ok enabled", style="green") if h.get("status") == "0" \
            else Text("fail disabled", style="red")

        table.add_row(
            h["host"],
            h.get("name", h["host"]) if h.get("name") != h["host"] else "—",
            ip,
            port,
            groups or "—",
            templates or "—",
            status_text,
        )

    console.print(table)
    console.print(f"[dim]{len(hosts)} host(s) total.[/dim]")


# ---------------------------------------------------------------------------
# zbx inventory apply
# ---------------------------------------------------------------------------

@app.command("apply")
def inventory_apply(
    path: Path = typer.Argument(
        ..., help="Path to inventory.yaml file."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would change without applying."
    ),
    env_file: Path = typer.Option(
        Path(".env"), "--env-file", "-e", help="Path to .env file."
    ),
    auto_approve: bool = typer.Option(
        False, "--auto-approve", "-y", help="Skip confirmation prompt."
    ),
) -> None:
    """Create or update hosts from an inventory YAML file."""
    loader = ConfigLoader()
    try:
        settings = loader.load_settings(env_file)
        inventory = loader.load_inventory(path)
    except (EnvironmentError, ValueError, FileNotFoundError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    if not inventory.hosts:
        console.print("[yellow]Inventory is empty.[/yellow]")
        raise typer.Exit(0)

    try:
        with ZabbixClient(settings) as client:
            existing = {h["host"]: h for h in client.list_hosts()}
            changes = _compute_inventory_diff(inventory, existing)
    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    _print_inventory_diff(changes)

    if not any(c["action"] != "ok" for c in changes):
        raise typer.Exit(0)

    if dry_run:
        console.print("[dim]Dry-run — no changes applied.[/dim]")
        raise typer.Exit(0)

    if not auto_approve:
        confirmed = typer.confirm("\nApply inventory changes?", default=False)
        if not confirmed:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(0)

    created = updated = 0
    try:
        with ZabbixClient(settings) as client:
            existing = {h["host"]: h for h in client.list_hosts()}
            for entry in inventory.hosts:
                group_ids = [client.ensure_hostgroup(g) for g in entry.groups]
                template_ids: list[str] = []
                for tname in entry.templates:
                    tmpl = client.get_template(tname)
                    if tmpl:
                        template_ids.append(str(tmpl["templateid"]))
                    else:
                        formatter.print_warning(f"Template '{tname}' not found — skipped.")

                cur = existing.get(entry.host)
                if cur is None:
                    hid = client.create_host(
                        host=entry.host,
                        name=entry.display_name,
                        ip=entry.ip,
                        port=entry.port,
                        group_ids=group_ids,
                        description=entry.description,
                        status=entry.status.zabbix_id,
                        template_ids=template_ids or None,
                    )
                    console.print(f"[green]  + created host '{entry.host}' (id={hid})[/green]")
                    created += 1
                else:
                    # Update groups, templates and status if changed
                    cur_groups = {g["groupid"] for g in cur.get("groups", [])}
                    cur_templates = {t["host"] for t in cur.get("parentTemplates", [])}
                    if (
                        set(group_ids) != cur_groups
                        or set(entry.templates) != cur_templates
                        or str(entry.status.zabbix_id) != str(cur.get("status", "0"))
                    ):
                        update_payload: dict = {
                            "hostid": cur["hostid"],
                            "groups": [{"groupid": gid} for gid in group_ids],
                            "status": entry.status.zabbix_id,
                        }
                        if template_ids:
                            update_payload["templates"] = [
                                {"templateid": tid} for tid in template_ids
                            ]
                        client._call("host.update", update_payload)  # noqa: SLF001
                        console.print(f"[yellow]  ~ updated host '{entry.host}'[/yellow]")
                        updated += 1
    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]ok Inventory applied: {created} created, {updated} updated.[/green]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_inventory_diff(
    inventory: Inventory, existing: dict
) -> list[dict]:
    changes = []
    for entry in inventory.hosts:
        cur = existing.get(entry.host)
        if cur is None:
            changes.append({"action": "create", "entry": entry})
        else:
            cur_groups = {g["name"] for g in cur.get("groups", [])}
            cur_templates = {t["host"] for t in cur.get("parentTemplates", [])}
            desired_groups = set(entry.groups)
            desired_templates = set(entry.templates)
            if (
                cur_groups != desired_groups
                or cur_templates != desired_templates
                or str(entry.status.zabbix_id) != str(cur.get("status", "0"))
            ):
                changes.append({"action": "update", "entry": entry, "current": cur})
            else:
                changes.append({"action": "ok", "entry": entry})
    return changes


def _print_inventory_diff(changes: list[dict]) -> None:
    for c in changes:
        entry: InventoryHost = c["entry"]
        if c["action"] == "create":
            console.print(
                f"[bold green]  + host: {entry.host}[/bold green]"
                f"  [dim]{entry.ip}:{entry.port}  groups={', '.join(entry.groups)}[/dim]"
            )
        elif c["action"] == "update":
            cur = c.get("current", {})
            cur_templates = {t["host"] for t in cur.get("parentTemplates", [])}
            desired_templates = set(entry.templates)
            parts = []
            cur_groups = {g["name"] for g in cur.get("groups", [])}
            if cur_groups != set(entry.groups):
                parts.append("groups")
            if cur_templates != desired_templates:
                added = desired_templates - cur_templates
                removed = cur_templates - desired_templates
                tmpl_desc = []
                if added:
                    tmpl_desc.append(f"+{', '.join(sorted(added))}")
                if removed:
                    tmpl_desc.append(f"-{', '.join(sorted(removed))}")
                parts.append(f"templates ({'; '.join(tmpl_desc)})")
            if str(entry.status.zabbix_id) != str(cur.get("status", "0")):
                parts.append("status")
            console.print(
                f"[bold yellow]  ~ host: {entry.host}[/bold yellow]"
                f"  [dim]({', '.join(parts) or 'changed'})[/dim]"
            )
    ok = sum(1 for c in changes if c["action"] == "ok")
    add = sum(1 for c in changes if c["action"] == "create")
    upd = sum(1 for c in changes if c["action"] == "update")
    console.print()
    console.rule("[bold]Inventory Summary[/bold]")
    parts = []
    if add:
        parts.append(f"[bold green]{add} to create[/bold green]")
    if upd:
        parts.append(f"[bold yellow]{upd} to update[/bold yellow]")
    if ok:
        parts.append(f"[dim]{ok} unchanged[/dim]")
    console.print("  ".join(parts) or "[dim]nothing to do[/dim]")
