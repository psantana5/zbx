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
        templates = loader.load_templates(path)
    except (FileNotFoundError, ValueError) as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    if verbose:
        for tmpl in templates:
            items_count = len(tmpl.items)
            trig_count = len(tmpl.triggers)
            disc_count = len(tmpl.discovery_rules)
            typer.echo(
                f"  ✓  {tmpl.template:<40}  "
                f"{items_count} item(s)  {trig_count} trigger(s)  {disc_count} discovery rule(s)"
            )

    formatter.print_validate_ok(templates)
