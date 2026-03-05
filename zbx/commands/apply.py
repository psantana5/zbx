"""zbx apply — create or update Zabbix resources from YAML config."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from zbx import formatter
from zbx.config_loader import ConfigLoader
from zbx.deployer import Deployer, HostDiff
from zbx.diff_engine import TemplateDiff
from zbx.plan_serializer import SavedPlan
from zbx.zabbix_client import ZabbixAPIError, ZabbixClient

console = Console()
app = typer.Typer()


def apply_cmd(
    path: Optional[Path] = typer.Argument(  # noqa: UP007
        None, help="Path to a YAML file or directory of configs."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Simulate changes without writing to Zabbix."
    ),
    env_file: Path = typer.Option(
        Path(".env"), "--env-file", "-e", help="Path to .env file with Zabbix credentials."
    ),
    auto_approve: bool = typer.Option(
        False, "--auto-approve", "-y", help="Skip interactive confirmation."
    ),
    from_plan: Optional[Path] = typer.Option(  # noqa: UP007
        None, "--from-plan", help="Apply a previously saved plan file (zbx plan --output)."
    ),
) -> None:
    """Apply configuration to Zabbix (templates, items, triggers, host links, macros)."""
    # --from-plan: load configs path from the saved plan file
    saved: SavedPlan | None = None
    if from_plan:
        try:
            saved = SavedPlan.load(from_plan)
        except ValueError as exc:
            formatter.print_error(str(exc))
            raise typer.Exit(1) from exc
        if path is None:
            path = saved.configs_path
        console.print(f"[dim]Loaded plan from [bold]{from_plan}[/bold] "
                      f"(created {saved.created_at[:19].replace('T', ' ')} UTC)[/dim]")
    elif path is None:
        formatter.print_error("Provide a configs path or use --from-plan <file>.")
        raise typer.Exit(1)

    loader = ConfigLoader()

    try:
        settings = loader.load_settings(env_file)
        templates, hosts = loader.load_all(path)
    except (FileNotFoundError, ValueError, EnvironmentError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    if not templates and not hosts:
        console.print("[yellow]No configuration found at the given path.[/yellow]")
        raise typer.Exit(0)

    # If we have a saved plan, show it directly; otherwise compute fresh
    if saved:
        template_diffs = saved.template_diffs
        host_diffs = saved.host_diffs
        formatter.print_diff(template_diffs, title="Saved Plan")
        formatter.print_host_diff(host_diffs, title="Saved Plan")
    else:
        try:
            with ZabbixClient(settings) as client:
                deployer = Deployer(client, dry_run=True)
                template_diffs = [deployer.plan(t) for t in templates]
                host_diffs = [deployer.plan_host(h) for h in hosts]
        except ZabbixAPIError as exc:
            formatter.print_error(f"Zabbix API: {exc}")
            raise typer.Exit(1) from exc

        formatter.print_diff(template_diffs, title="Plan")
        formatter.print_host_diff(host_diffs, title="Plan")

    no_changes = not any(d.has_changes for d in template_diffs) and \
                 not any(d.has_changes for d in host_diffs)
    if no_changes:
        raise typer.Exit(0)

    if dry_run:
        console.print("[dim]Dry-run mode — no changes applied.[/dim]")
        raise typer.Exit(0)

    if not auto_approve:
        confirmed = typer.confirm("\nDo you want to apply these changes?", default=False)
        if not confirmed:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(0)

    t_results: list[TemplateDiff] = []
    h_results: list[HostDiff] = []

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            total = len(templates) + len(hosts)
            task = progress.add_task("Applying…", total=total)
            with ZabbixClient(settings) as client:
                deployer = Deployer(client, dry_run=False)
                for tmpl in templates:
                    progress.update(task, description=f"Applying template '{tmpl.template}'…")
                    t_results.append(deployer.apply(tmpl))
                    progress.advance(task)
                for host in hosts:
                    progress.update(task, description=f"Configuring host '{host.host}'…")
                    h_results.append(deployer.apply_host(host))
                    progress.advance(task)
    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    formatter.print_apply_result(t_results)
    applied_hosts = [h for h in h_results if h.has_changes]
    if applied_hosts:
        console.print(f"[green]ok Configured {len(applied_hosts)} host(s).[/green]")
