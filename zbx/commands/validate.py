"""zbx validate — validate YAML configuration files against the schema."""

from __future__ import annotations

from pathlib import Path

import typer

from zbx import formatter
from zbx.config_loader import ConfigLoader

app = typer.Typer()


def validate_cmd(
    path: Path = typer.Argument(..., help="Path to a YAML file or directory of configs."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="List each validated template."),
) -> None:
    """Validate YAML configuration files without connecting to Zabbix."""
    loader = ConfigLoader()
    try:
        templates, hosts = loader.load_all(path)
    except (FileNotFoundError, ValueError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    if verbose:
        for tmpl in templates:
            typer.echo(
                f"  ok  {tmpl.template:<40}  "
                f"{len(tmpl.items)} item(s)  {len(tmpl.triggers)} trigger(s)  "
                f"{len(tmpl.discovery_rules)} discovery rule(s)"
            )
        for host in hosts:
            typer.echo(
                f"  ok  host:{host.host:<35}  "
                f"{len(host.templates)} template link(s)  {len(host.macros)} macro(s)"
            )

    formatter.print_validate_ok(templates, hosts)
