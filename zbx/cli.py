"""zbx — Zabbix configuration as code.

Entry point for the CLI. All commands are registered here.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler

from zbx import __version__
from zbx.commands.apply import apply_cmd
from zbx.commands.agent import app as agent_app
from zbx.commands.diff import diff_cmd
from zbx.commands.export import export_cmd
from zbx.commands.inventory import app as inventory_app
from zbx.commands.plan import plan_cmd
from zbx.commands.validate import validate_cmd

console = Console(stderr=True)

app = typer.Typer(
    name="zbx",
    help=(
        "[bold cyan]zbx[/bold cyan] — Zabbix configuration as code.\n\n"
        "Manage Zabbix templates, items, triggers and discovery rules via YAML files "
        "and Git — the same mental model as Terraform or Ansible."
    ),
    rich_markup_mode="rich",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

# ---------------------------------------------------------------------------
# Register commands
# ---------------------------------------------------------------------------

app.command("apply", help="Create or update Zabbix resources from YAML config.")(apply_cmd)
app.command("plan", help="Show what changes would be applied (dry run).")(plan_cmd)
app.command("diff", help="Show differences between local config and Zabbix.")(diff_cmd)
app.command("validate", help="Validate YAML files against the schema.")(validate_cmd)
app.command("export", help="Export a Zabbix template to YAML (for Git migration).")(export_cmd)
app.add_typer(inventory_app, name="inventory", help="Manage host inventory (list, apply).")
app.add_typer(agent_app, name="agent", help="Deploy scripts and UserParameters to hosts.")


# ---------------------------------------------------------------------------
# Global options callback
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"zbx {__version__}")
        raise typer.Exit()


@app.callback()
def _global_options(
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable debug-level logging.",
        is_eager=False,
    ),
    version: Optional[bool] = typer.Option(  # noqa: UP007
        None,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Configure logging before any command runs."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                show_path=verbose,
                markup=True,
            )
        ],
    )
    # Silence noisy third-party loggers unless user asked for verbose
    if not verbose:
        for noisy in ("urllib3", "requests", "charset_normalizer"):
            logging.getLogger(noisy).setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
