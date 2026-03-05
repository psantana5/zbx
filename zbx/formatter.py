"""Rich-based output formatting for diffs, plans, and apply results."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from zbx.deployer import HostDiff
from zbx.diff_engine import ChangeType, ResourceChange, TemplateDiff

console = Console()

# Colour palette
_COLOR = {
    ChangeType.ADD: "green",
    ChangeType.MODIFY: "yellow",
    ChangeType.REMOVE: "red",
    ChangeType.UNCHANGED: "dim",
}
_SYMBOL = {
    ChangeType.ADD: "+",
    ChangeType.MODIFY: "~",
    ChangeType.REMOVE: "-",
    ChangeType.UNCHANGED: " ",
}
_RESOURCE_PREFIX = {
    "item": "item",
    "trigger": "trigger",
    "discovery_rule": "discovery_rule",
}


def print_diff(diffs: list[TemplateDiff], *, title: str = "Plan") -> None:
    """Print a Terraform-style diff for all templates."""
    if not any(d.has_changes for d in diffs):
        console.print("[green]ok No changes. Infrastructure is up-to-date.[/green]")
        return

    for diff in diffs:
        if diff.has_changes:  # skip templates with no changes (avoids empty panels)
            _print_template_diff(diff)

    _print_summary(diffs, title=title)


def _print_template_diff(diff: TemplateDiff) -> None:
    sym = _SYMBOL[diff.template_change]
    col = _COLOR[diff.template_change]

    header = Text()
    header.append(f"{sym} ", style=f"bold {col}")
    header.append("template: ", style="bold")
    header.append(diff.template_name, style=f"bold {col}")
    if diff.template_id:
        header.append(f"  [dim](id={diff.template_id})[/dim]")

    lines: list[Text] = []

    # Template-level field changes
    for fc in diff.field_changes:
        line = Text()
        line.append("    ~ ", style="bold yellow")
        line.append(f"{fc.field}: ", style="yellow")
        line.append(str(fc.old_value), style="red")
        line.append(" → ", style="dim")
        line.append(str(fc.new_value), style="green")
        lines.append(line)

    # Resource changes — only non-unchanged
    for rc in diff.resource_changes:
        if rc.type == ChangeType.UNCHANGED:
            continue
        lines.append(_format_resource_change(rc))

    # Warnings (e.g. unresolvable master_item_key)
    for w in diff.warnings:
        line = Text()
        line.append("  ! ", style="bold yellow")
        line.append(w, style="yellow")
        lines.append(line)

    if lines:
        body = Text("\n").join(lines)
    else:
        # Template-level change (add/update) but no items/triggers/discovery rules
        body = Text("  (no items, triggers, or discovery rules)", style="dim")

    panel = Panel(body, title=header, title_align="left", border_style=col, padding=(0, 1))
    console.print(panel)


def _format_resource_change(rc: ResourceChange) -> Text:
    sym = _SYMBOL[rc.type]
    col = _COLOR[rc.type]
    prefix = _RESOURCE_PREFIX.get(rc.resource_type, rc.resource_type)

    line = Text()
    line.append(f"  {sym} ", style=f"bold {col}")
    line.append(f"{prefix}: ", style=col)
    line.append(rc.name, style=f"bold {col}")
    if rc.key and rc.key != rc.name:
        line.append(f"  [dim]({rc.key})[/dim]")

    if rc.type == ChangeType.REMOVE:
        line.append("  [dim italic](not in config — skipped)[/dim italic]")

    for fc in rc.field_changes:
        sub = Text()
        sub.append(f"\n      {fc.field}: ", style="dim")
        sub.append(str(fc.old_value), style="red")
        sub.append(" → ", style="dim")
        sub.append(str(fc.new_value), style="green")
        line.append_text(sub)

    return line


def _print_summary(diffs: list[TemplateDiff], *, title: str) -> None:
    totals = {ChangeType.ADD: 0, ChangeType.MODIFY: 0, ChangeType.REMOVE: 0}
    for diff in diffs:
        s = diff.summary
        totals[ChangeType.ADD] += s.get("add", 0)
        totals[ChangeType.MODIFY] += s.get("modify", 0)
        totals[ChangeType.REMOVE] += s.get("remove", 0)

    parts: list[Text] = []
    if totals[ChangeType.ADD]:
        parts.append(Text(f"{totals[ChangeType.ADD]} to add", style="bold green"))
    if totals[ChangeType.MODIFY]:
        parts.append(Text(f"{totals[ChangeType.MODIFY]} to modify", style="bold yellow"))
    if totals[ChangeType.REMOVE]:
        parts.append(Text(f"{totals[ChangeType.REMOVE]} to remove", style="bold red"))

    summary = Text(", ").join(parts) if parts else Text("0 changes")
    console.print()
    console.rule(f"[bold]{title} Summary[/bold]")
    console.print(Text(f"{title}: ") + summary)


def print_apply_result(diffs: list[TemplateDiff]) -> None:
    applied = [d for d in diffs if d.has_changes]
    if not applied:
        console.print("[green]ok Nothing to apply.[/green]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Template", style="cyan")
    table.add_column("Added", style="green", justify="right")
    table.add_column("Modified", style="yellow", justify="right")
    table.add_column("Removed", style="red", justify="right")

    for diff in applied:
        s = diff.summary
        table.add_row(
            diff.template_name,
            str(s.get("add", 0)),
            str(s.get("modify", 0)),
            str(s.get("remove", 0)),
        )

    console.print(table)
    console.print("[green]ok Apply complete.[/green]")


def print_validate_ok(templates: list, hosts: list | None = None) -> None:
    t = len(templates)
    h = len(hosts) if hosts else 0
    parts = []
    if t:
        parts.append(f"{t} template(s)")
    if h:
        parts.append(f"{h} host config(s)")
    noun = " and ".join(parts) or "0 documents"
    console.print(f"[green]ok Validated {noun} — no schema errors.[/green]")


def print_host_diff(host_diffs: list[HostDiff], *, title: str = "Plan") -> None:
    """Print Terraform-style diff for host configuration changes."""
    for diff in host_diffs:
        if not diff.found:
            console.print(
                f"[bold yellow]! host '{diff.host_name}' not found in Zabbix — "
                "create it first, then run zbx apply again.[/bold yellow]"
            )
            continue
        if not diff.has_changes:
            continue

        lines: list[Text] = []

        for tname in diff.templates_to_link:
            line = Text()
            line.append("  + ", style="bold green")
            line.append("link template: ", style="green")
            line.append(tname, style="bold green")
            lines.append(line)

        for mc in diff.macro_changes:
            if mc.type == ChangeType.UNCHANGED:
                continue
            col = _COLOR[mc.type]
            sym = _SYMBOL[mc.type]
            line = Text()
            line.append(f"  {sym} ", style=f"bold {col}")
            line.append("macro: ", style=col)
            line.append(mc.macro, style=f"bold {col}")
            if mc.type == ChangeType.MODIFY:
                line.append(f"\n      value: ", style="dim")
                line.append(mc.old_value, style="red")
                line.append(" → ", style="dim")
                line.append(mc.new_value, style="green")
            lines.append(line)

        body = Text("\n").join(lines)
        header = Text()
        header.append("~ ", style="bold yellow")
        header.append("host: ", style="bold")
        header.append(diff.host_name, style="bold yellow")
        console.print(Panel(body, title=header, title_align="left", border_style="yellow", padding=(0, 1)))


def print_error(message: str) -> None:
    console.print(f"[bold red]fail Error:[/bold red] {message}")


def print_warning(message: str) -> None:
    console.print(f"[bold yellow]! Warning:[/bold yellow] {message}")
