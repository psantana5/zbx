"""zbx check — discover and deploy bundled community monitoring checks.

Commands:
    zbx check list              Table of all bundled checks
    zbx check info <name>       Show template details for a check
    zbx check install <name>    Copy check to configs/checks/ then apply to Zabbix
"""

from __future__ import annotations

import importlib.resources
import shutil
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

def _bundled_checks_dir() -> Path:
    """Return the path to bundled checks shipped inside the zbx package."""
    # Works for both editable installs and pip-installed wheels
    pkg_root = Path(importlib.resources.files("zbx").__str__())  # type: ignore[arg-type]
    bundled = pkg_root / "checks"
    if bundled.exists():
        return bundled
    # Fallback: repo layout (configs/checks relative to cwd)
    fallback = Path("configs/checks")
    if fallback.exists():
        return fallback
    raise FileNotFoundError(
        "Bundled checks not found. Re-install zbxctl: pip install --upgrade zbxctl"
    )


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
        tmpl = raw[0] if isinstance(raw, list) else raw

        found.append({
            "name": folder.name,
            "template": tmpl.get("template", folder.name),
            "description": (tmpl.get("description") or "")[:60],
            "items": len(tmpl.get("items", [])),
            "triggers": len(tmpl.get("triggers", [])),
            "discovery_rules": len(tmpl.get("discovery_rules", [])),
            "has_script": (folder / "scripts").exists() or bool(tmpl.get("agent")),
            "path": folder,
        })
    return found


def _resolve_bundled(name: str) -> Path:
    """Find a bundled check by exact or partial name."""
    checks_dir = _bundled_checks_dir()
    candidates = [d for d in checks_dir.iterdir() if d.is_dir() and name.lower() in d.name.lower()]
    if not candidates:
        rprint(f"[red]✗[/red] No bundled check matching '{name}'.")
        rprint("  Run [bold]zbx check list[/bold] to see available checks.")
        raise typer.Exit(1)
    if len(candidates) > 1:
        rprint(f"[yellow]![/yellow] Ambiguous — '{name}' matches: {', '.join(c.name for c in candidates)}")
        rprint("  Use the exact name shown in [bold]zbx check list[/bold].")
        raise typer.Exit(1)
    return candidates[0]


# ---------------------------------------------------------------------------
# zbx check list
# ---------------------------------------------------------------------------

@app.command("list")
def check_list() -> None:
    """List all bundled monitoring checks."""
    try:
        checks_dir = _bundled_checks_dir()
    except FileNotFoundError as exc:
        rprint(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc

    checks = _find_checks(checks_dir)
    if not checks:
        rprint("[yellow]No bundled checks found.[/yellow]")
        raise typer.Exit(0)

    # Mark which are already installed locally
    local_names = {d.name for d in _DEFAULT_CHECKS_DIR.iterdir() if d.is_dir()} \
        if _DEFAULT_CHECKS_DIR.exists() else set()

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Template")
    table.add_column("Items", justify="right")
    table.add_column("Triggers", justify="right")
    table.add_column("Script", justify="center")
    table.add_column("Installed", justify="center")
    table.add_column("Description", style="dim")

    for c in checks:
        installed = c["name"] in local_names
        table.add_row(
            c["name"],
            c["template"],
            str(c["items"]),
            str(c["triggers"]),
            "[green]✓[/green]" if c["has_script"] else "[dim]—[/dim]",
            "[green]✓[/green]" if installed else "[dim]—[/dim]",
            c["description"] or "—",
        )

    console.print()
    console.print(table)
    console.print(
        f"\n[dim]{len(checks)} bundled check(s)  "
        f"— [bold]zbx check install <name>[/bold] to copy + deploy[/dim]\n"
    )


# ---------------------------------------------------------------------------
# zbx check info <name>
# ---------------------------------------------------------------------------

@app.command("info")
def check_info(
    name: Annotated[str, typer.Argument(help="Check name (exact or partial).")],
) -> None:
    """Show full details for a bundled check."""
    folder = _resolve_bundled(name)
    check_yaml = folder / "check.yaml"

    with open(check_yaml) as f:
        raw = yaml.safe_load(f)
    tmpl = raw[0] if isinstance(raw, list) else raw

    console.print()
    console.print(f"[bold cyan]{folder.name}[/bold cyan]")
    console.print(f"  Template : [bold]{tmpl.get('template', '—')}[/bold]")
    if tmpl.get("description"):
        console.print(f"  Desc     : {tmpl['description'][:100]}")

    items = tmpl.get("items", [])
    if items:
        console.print(f"\n  [bold]Items ({len(items)}):[/bold]")
        for it in items:
            console.print(f"    [dim]{it.get('key', '?')}[/dim]  {it.get('name', '')}")

    triggers = tmpl.get("triggers", [])
    if triggers:
        console.print(f"\n  [bold]Triggers ({len(triggers)}):[/bold]")
        for tr in triggers:
            sev = tr.get("severity", "average")
            color = {"disaster": "red", "high": "red", "average": "yellow",
                     "warning": "yellow"}.get(sev, "dim")
            console.print(f"    [[{color}]{sev}[/{color}]]  {tr.get('name', '?')}")

    rules = tmpl.get("discovery_rules", [])
    if rules:
        console.print(f"\n  [bold]Discovery rules ({len(rules)}):[/bold]")
        for r in rules:
            console.print(f"    {r.get('name', '?')}  [dim]({r.get('key', '?')})[/dim]")

    scripts_dir = folder / "scripts"
    if scripts_dir.exists():
        scripts = list(scripts_dir.iterdir())
        console.print(f"\n  [bold]Scripts ({len(scripts)}):[/bold]")
        for s in scripts:
            console.print(f"    {s.name}")

    console.print(f"\n  Install: [bold]zbx check install {folder.name}[/bold]")
    console.print()


# ---------------------------------------------------------------------------
# zbx check install <name>
# ---------------------------------------------------------------------------

@app.command("install")
def check_install(
    name: Annotated[str, typer.Argument(help="Check name (exact or partial).")],
    dest: Annotated[Path, typer.Option("--dest", "-d",
        help="Destination directory (default: configs/checks/)")] = _DEFAULT_CHECKS_DIR,
    apply: Annotated[bool, typer.Option("--apply/--no-apply",
        help="Also apply the template to Zabbix (default: yes).")] = True,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n")] = False,
    env_file: Annotated[Path, typer.Option("--env-file", "-e")] = Path(".env"),
    auto_approve: Annotated[bool, typer.Option("--auto-approve", "-y")] = False,
) -> None:
    """Copy a bundled check to your project and optionally apply it to Zabbix.

    Two steps:
      1. Copy  configs/checks/<name>/  from the zbxctl package into your project
      2. Run   zbx apply configs/checks/<name>/  (skip with --no-apply)

    Example:
      zbx check install postgresql
      zbx check install redis --no-apply   # copy only, apply later
    """
    bundled = _resolve_bundled(name)
    target = dest / bundled.name

    # ── Step 1: copy files ────────────────────────────────────────────────
    console.print()
    if target.exists():
        console.print(f"[yellow]![/yellow]  {target} already exists — skipping copy.")
    else:
        if dry_run:
            console.print(f"[cyan](dry-run)[/cyan]  Would copy {bundled} → {target}")
        else:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(bundled, target)
            console.print(f"[green]✓[/green]  Copied [bold]{bundled.name}[/bold] → {target}")

    # Show what was copied
    check_yaml = (bundled if dry_run else target) / "check.yaml"
    if check_yaml.exists():
        with open(check_yaml) as f:
            raw = yaml.safe_load(f)
        tmpl = raw[0] if isinstance(raw, list) else raw
        n_items    = len(tmpl.get("items", []))
        n_triggers = len(tmpl.get("triggers", []))
        console.print(
            f"   template=[bold]{tmpl.get('template', bundled.name)}[/bold]  "
            f"items={n_items}  triggers={n_triggers}"
        )

    if not apply:
        console.print(f"\n[dim]Run [bold]zbx apply {target}[/bold] when ready.[/dim]\n")
        return

    # ── Step 2: apply to Zabbix ───────────────────────────────────────────
    if dry_run:
        console.print(f"\n[cyan](dry-run)[/cyan]  Would apply {target} to Zabbix.\n")
        return

    console.print(f"\n[bold]Applying template to Zabbix…[/bold]")
    from zbx.commands.apply import apply_cmd  # noqa: PLC0415
    try:
        apply_cmd(
            path=target,
            dry_run=False,
            env_file=env_file,
            auto_approve=auto_approve,
        )
    except SystemExit:
        pass

