"""zbx check — discover and deploy bundled community monitoring checks.

Commands:
    zbx check list              Table of all checks in configs/checks/
    zbx check info <name>       Show template details for a check
    zbx check install <name> <host>
                                Apply template to Zabbix + deploy agent in one step
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
import yaml
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from zbx.config_loader import ConfigLoader

console = Console()
app = typer.Typer(help="Discover and deploy bundled monitoring checks.")

_DEFAULT_CHECKS_DIR = Path("configs/checks")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_checks(checks_dir: Path) -> list[dict]:
    """Return metadata for every check.yaml under checks_dir."""
    found = []
    for check_yaml in sorted(checks_dir.glob("*/check.yaml")):
        folder = check_yaml.parent
        try:
            with open(check_yaml) as f:
                raw = yaml.safe_load(f)
        except Exception:  # noqa: BLE001
            continue
        if not raw:
            continue
        # Support both single-template and list format
        if isinstance(raw, list):
            tmpl = raw[0] if raw else {}
        else:
            tmpl = raw

        items = tmpl.get("items", [])
        triggers = tmpl.get("triggers", [])
        discovery_rules = tmpl.get("discovery_rules", [])
        has_agent = "agent" in tmpl

        found.append({
            "name": folder.name,
            "template": tmpl.get("template", folder.name),
            "description": (tmpl.get("description") or "")[:60],
            "items": len(items),
            "triggers": len(triggers),
            "discovery_rules": len(discovery_rules),
            "has_agent": has_agent,
            "path": folder,
        })
    return found


def _resolve_check(name: str, checks_dir: Path) -> Path:
    """Find the check folder by exact or partial name."""
    candidates = [d for d in checks_dir.iterdir() if d.is_dir() and name.lower() in d.name.lower()]
    if not candidates:
        rprint(f"[red]fail[/red] No check matching '{name}' in {checks_dir}.")
        rprint(f"  Run [bold]zbx check list[/bold] to see available checks.")
        raise typer.Exit(1)
    if len(candidates) > 1:
        names = ", ".join(c.name for c in candidates)
        rprint(f"[yellow]![/yellow] Ambiguous name '{name}' matches: {names}")
        rprint("  Use the exact folder name.")
        raise typer.Exit(1)
    return candidates[0]


# ---------------------------------------------------------------------------
# zbx check list
# ---------------------------------------------------------------------------

@app.command("list")
def check_list(
    checks_dir: Annotated[Path, typer.Option("--checks-dir", help="Root checks folder.")] = _DEFAULT_CHECKS_DIR,
) -> None:
    """List all bundled monitoring checks."""
    if not checks_dir.exists():
        rprint(f"[yellow]![/yellow]  Checks directory not found: {checks_dir}")
        raise typer.Exit(1)

    checks = _find_checks(checks_dir)
    if not checks:
        rprint(f"[yellow]No checks found in {checks_dir}[/yellow]")
        raise typer.Exit(0)

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Template ID")
    table.add_column("Items", justify="right")
    table.add_column("Triggers", justify="right")
    table.add_column("Rules", justify="right")
    table.add_column("Agent", justify="center")
    table.add_column("Description", style="dim")

    for c in checks:
        table.add_row(
            c["name"],
            c["template"],
            str(c["items"]),
            str(c["triggers"]),
            str(c["discovery_rules"]),
            "[green]✓[/green]" if c["has_agent"] else "[dim]—[/dim]",
            c["description"] or "—",
        )

    console.print()
    console.print(table)
    console.print(
        f"\n[dim]{len(checks)} check(s) — use [bold]zbx check install <name> <host>[/bold] "
        f"to deploy one.[/dim]\n"
    )


# ---------------------------------------------------------------------------
# zbx check info <name>
# ---------------------------------------------------------------------------

@app.command("info")
def check_info(
    name: Annotated[str, typer.Argument(help="Check name (folder name or partial match).")],
    checks_dir: Annotated[Path, typer.Option("--checks-dir")] = _DEFAULT_CHECKS_DIR,
) -> None:
    """Show details for a specific check."""
    folder = _resolve_check(name, checks_dir)
    check_yaml = folder / "check.yaml"

    with open(check_yaml) as f:
        raw = yaml.safe_load(f)
    tmpl = raw[0] if isinstance(raw, list) else raw

    console.print()
    console.print(f"[bold cyan]{folder.name}[/bold cyan]")
    console.print(f"  Template : [bold]{tmpl.get('template', '—')}[/bold]")
    if tmpl.get("name"):
        console.print(f"  Name     : {tmpl['name']}")
    if tmpl.get("description"):
        console.print(f"  Desc     : {tmpl['description'][:80]}")
    console.print()

    items = tmpl.get("items", [])
    if items:
        console.print(f"  [bold]Items ({len(items)}):[/bold]")
        for it in items:
            console.print(f"    [dim]{it.get('key', '?')}[/dim]  {it.get('name', '')}")

    triggers = tmpl.get("triggers", [])
    if triggers:
        console.print(f"\n  [bold]Triggers ({len(triggers)}):[/bold]")
        for tr in triggers:
            sev = tr.get("severity", "average")
            sev_color = {"disaster": "red", "high": "red", "average": "yellow",
                         "warning": "yellow"}.get(sev, "dim")
            console.print(f"    [[{sev_color}]{sev}[/{sev_color}]]  {tr.get('name', '?')}")

    rules = tmpl.get("discovery_rules", [])
    if rules:
        console.print(f"\n  [bold]Discovery rules ({len(rules)}):[/bold]")
        for r in rules:
            console.print(f"    {r.get('name', '?')}  [dim]({r.get('key', '?')})[/dim]")

    agent = tmpl.get("agent")
    if agent:
        scripts = agent.get("scripts", [])
        ups = agent.get("userparameters", [])
        console.print(f"\n  [bold]Agent deployment:[/bold]")
        for s in scripts:
            console.print(f"    script  {s.get('source', '?')}  →  {s.get('dest', '?')}")
        for u in ups:
            for p in u.get("parameters", []):
                console.print(f"    param   [dim]{p.get('key', '?')}[/dim]")
        console.print(
            f"\n  Deploy with: [bold]zbx check install {folder.name} <host>[/bold]"
        )
    else:
        console.print("\n  [dim](no agent block — uses built-in Zabbix agent keys)[/dim]")

    console.print()

    readme = folder / "README.md"
    if readme.exists():
        console.print(f"  [dim]README: {readme}[/dim]")
    console.print()


# ---------------------------------------------------------------------------
# zbx check install <name> <host>
# ---------------------------------------------------------------------------

@app.command("install")
def check_install(
    name: Annotated[str, typer.Argument(help="Check name (folder name or partial match).")],
    host: Annotated[Optional[str], typer.Argument(help="Hostname from inventory.yaml (required for agent deploy).")] = None,
    checks_dir: Annotated[Path, typer.Option("--checks-dir")] = _DEFAULT_CHECKS_DIR,
    inventory: Annotated[Path, typer.Option("--inventory", "-i")] = Path("inventory.yaml"),
    env_file: Annotated[Path, typer.Option("--env-file", "-e")] = Path(".env"),
    auto_approve: Annotated[bool, typer.Option("--auto-approve", "-y")] = False,
    template_only: Annotated[bool, typer.Option("--template-only", help="Only apply template, skip agent deploy.")] = False,
    agent_only: Annotated[bool, typer.Option("--agent-only", help="Only deploy agent, skip template apply.")] = False,
) -> None:
    """Apply a check's template to Zabbix and deploy its agent script to a host.

    Combines:  zbx apply <check>/  +  zbx agent deploy <host> --from-check <check>/
    """
    folder = _resolve_check(name, checks_dir)

    # ---- Step 1: apply template ----
    if not agent_only:
        rprint(f"\n[bold]Step 1 — Apply template: [cyan]{folder.name}[/cyan][/bold]")
        from zbx.commands.apply import apply_cmd  # noqa: PLC0415
        import click  # noqa: PLC0415
        ctx = typer.Context(typer.main.get_command(typer.Typer()))  # minimal ctx
        try:
            apply_cmd(
                path=folder,
                dry_run=False,
                env_file=env_file,
                auto_approve=auto_approve,
            )
        except SystemExit:
            pass

    # ---- Step 2: agent deploy ----
    if template_only or host is None:
        if not template_only and host is None:
            rprint("\n[dim]No host specified — skipping agent deploy.[/dim]")
            rprint(f"  To deploy the agent later:\n  [bold]zbx agent deploy <host> --from-check {folder}[/bold]\n")
        return

    rprint(f"\n[bold]Step 2 — Deploy agent: [cyan]{host}[/cyan] ← {folder.name}[/bold]")
    from zbx.commands.agent import agent_deploy_cmd  # noqa: PLC0415
    try:
        agent_deploy_cmd(
            hostname=host,
            inventory=inventory,
            dry_run=False,
            auto_approve=auto_approve,
            from_check=folder,
        )
    except SystemExit:
        pass
