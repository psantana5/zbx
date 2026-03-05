"""zbx plan — show what changes would be applied (dry run)."""

from __future__ import annotations

from pathlib import Path

import typer

from zbx import formatter
from zbx.config_loader import ConfigLoader
from zbx.deployer import Deployer
from zbx.zabbix_client import ZabbixAPIError, ZabbixClient

app = typer.Typer()


def plan_cmd(
    path: Path = typer.Argument(..., help="Path to a YAML file or directory of configs."),
    env_file: Path = typer.Option(
        Path(".env"), "--env-file", "-e", help="Path to .env file with Zabbix credentials."
    ),
) -> None:
    """Show what changes would be made without applying them."""
    loader = ConfigLoader()

    try:
        settings = loader.load_settings(env_file)
        templates = loader.load_templates(path)
    except (FileNotFoundError, ValueError, EnvironmentError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        with ZabbixClient(settings) as client:
            deployer = Deployer(client, dry_run=True)
            diffs = [deployer.plan(tmpl) for tmpl in templates]
    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    formatter.print_diff(diffs, title="Plan")
