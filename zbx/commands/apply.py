"""zbx apply — create or update Zabbix resources from YAML config."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from zbx import formatter
from zbx.config_loader import ConfigLoader
from zbx.deployer import Deployer
from zbx.diff_engine import TemplateDiff
from zbx.zabbix_client import ZabbixAPIError, ZabbixClient

console = Console()
app = typer.Typer()


def apply_cmd(
    path: Path = typer.Argument(..., help="Path to a YAML file or directory of configs."),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Simulate changes without writing to Zabbix."
    ),
    env_file: Path = typer.Option(
        Path(".env"), "--env-file", "-e", help="Path to .env file with Zabbix credentials."
    ),
    auto_approve: bool = typer.Option(
        False, "--auto-approve", "-y", help="Skip interactive confirmation."
    ),
) -> None:
    """Apply configuration to Zabbix (create or update templates, items, triggers)."""
    loader = ConfigLoader()

    try:
        settings = loader.load_settings(env_file)
        templates = loader.load_templates(path)
    except (FileNotFoundError, ValueError, EnvironmentError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    if not templates:
        console.print("[yellow]No templates found at the given path.[/yellow]")
        raise typer.Exit(0)

    try:
        with ZabbixClient(settings) as client:
            deployer = Deployer(client, dry_run=True)
            diffs = [deployer.plan(tmpl) for tmpl in templates]
    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    # Show the plan first
    formatter.print_diff(diffs, title="Plan")

    if not any(d.has_changes for d in diffs):
        raise typer.Exit(0)

    if dry_run:
        console.print("[dim]Dry-run mode — no changes applied.[/dim]")
        raise typer.Exit(0)

    # Confirmation prompt
    if not auto_approve:
        confirmed = typer.confirm("\nDo you want to apply these changes?", default=False)
        if not confirmed:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(0)

    # Apply
    results: list[TemplateDiff] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Applying…", total=len(templates))
        try:
            with ZabbixClient(settings) as client:
                deployer = Deployer(client, dry_run=False)
                for tmpl in templates:
                    progress.update(task, description=f"Applying '{tmpl.template}'…")
                    results.append(deployer.apply(tmpl))
                    progress.advance(task)
        except ZabbixAPIError as exc:
            formatter.print_error(f"Zabbix API: {exc}")
            raise typer.Exit(1) from exc

    formatter.print_apply_result(results)
