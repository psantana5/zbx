"""zbx schema — print JSON Schema or Markdown reference for the YAML config format."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown

from zbx.models import Template

console = Console()
app = typer.Typer()


class OutputFormat(str, Enum):
    json = "json"
    markdown = "markdown"


def schema_cmd(
    fmt: OutputFormat = typer.Option(
        OutputFormat.markdown,
        "--format",
        "-f",
        help="Output format: markdown (default) or json.",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write to file instead of stdout.",
    ),
) -> None:
    """Show the JSON Schema (or Markdown reference) for zbx YAML config files."""
    if fmt == OutputFormat.json:
        text = json.dumps(Template.model_json_schema(), indent=2)
    else:
        text = _markdown_reference()

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
        console.print(f"[green]✓ Schema written to {output}[/green]")
    elif fmt == OutputFormat.markdown:
        console.print(Markdown(text))
    else:
        print(text)


# ---------------------------------------------------------------------------
# Markdown reference generator
# ---------------------------------------------------------------------------

_FIELD_DOCS: dict[str, dict[str, str]] = {
    # Template root
    "template":    {"type": "string", "required": "yes", "description": "Zabbix `host` identifier (no spaces)."},
    "name":        {"type": "string", "required": "no",  "description": "Human-readable display name shown in Zabbix UI."},
    "description": {"type": "string", "required": "no",  "description": "Free-text description of the template."},
    "groups":      {"type": "list[string]", "required": "no", "description": "Host groups this template belongs to. Default: `[Templates]`."},
    # Items
    "items[].name":        {"type": "string", "required": "yes", "description": "Display name of the item."},
    "items[].key":         {"type": "string", "required": "yes", "description": "Zabbix item key (e.g. `system.cpu.util`)."},
    "items[].interval":    {"type": "string", "required": "no",  "description": "Collection interval. Accepts Zabbix format: `60s`, `1m`, `5m`. Default: `60s`."},
    "items[].type":        {"type": "string", "required": "no",  "description": "Item type. One of: `zabbix_agent`, `zabbix_trapper`, `snmp`, `calculated`, `dependent`, … Default: `zabbix_agent`."},
    "items[].value_type":  {"type": "string", "required": "no",  "description": "Data type: `float`, `int`, `str`, `text`, `log`. Default: `float`."},
    "items[].units":       {"type": "string", "required": "no",  "description": "Unit suffix shown in graphs (e.g. `%`, `B`, `rpm`)."},
    "items[].params":      {"type": "string", "required": "no",  "description": "Formula for `calculated` items (type=15)."},
    "items[].master_item_key": {"type": "string", "required": "no", "description": "Key of the master item for `dependent` items (type=18)."},
    "items[].history":     {"type": "string", "required": "no",  "description": "History retention. Default: `90d`."},
    "items[].trends":      {"type": "string", "required": "no",  "description": "Trend retention. Default: `365d`."},
    "items[].enabled":     {"type": "bool",   "required": "no",  "description": "Whether the item is active. Default: `true`."},
    "items[].description": {"type": "string", "required": "no",  "description": "Item description."},
    "items[].tags":        {"type": "list",   "required": "no",  "description": "Tags: list of `{tag: name, value: val}` pairs."},
    # Triggers
    "triggers[].name":       {"type": "string", "required": "yes", "description": "Trigger name shown in alerts."},
    "triggers[].expression": {"type": "string", "required": "yes", "description": "Zabbix trigger expression."},
    "triggers[].severity":   {"type": "string", "required": "no",  "description": "One of: `info`, `warning`, `average`, `high`, `disaster`. Default: `average`."},
    "triggers[].recovery_expression": {"type": "string", "required": "no", "description": "Recovery expression (leave blank for default recovery)."},
    "triggers[].description": {"type": "string", "required": "no", "description": "Operational data / description."},
    "triggers[].enabled":    {"type": "bool",   "required": "no",  "description": "Default: `true`."},
    "triggers[].tags":       {"type": "list",   "required": "no",  "description": "Tags: list of `{tag: name, value: val}` pairs."},
    # Discovery rules
    "discovery_rules[].name":     {"type": "string", "required": "yes", "description": "Rule display name."},
    "discovery_rules[].key":      {"type": "string", "required": "yes", "description": "LLD rule key (e.g. `vfs.fs.discovery`)."},
    "discovery_rules[].interval": {"type": "string", "required": "no",  "description": "Discovery interval. Default: `1h`."},
    "discovery_rules[].type":     {"type": "string", "required": "no",  "description": "Same values as `items[].type`. Default: `zabbix_agent`."},
    "discovery_rules[].master_item_key": {"type": "string", "required": "no", "description": "Master item key for `dependent` discovery rules."},
    "discovery_rules[].filter":   {"type": "list",   "required": "no",  "description": "LLD filter conditions: `[{macro: '{#FSTYPE}', value: 'xfs'}]`."},
    "discovery_rules[].item_prototypes":    {"type": "list", "required": "no", "description": "Item prototypes (same fields as `items[]`)."},
    "discovery_rules[].trigger_prototypes": {"type": "list", "required": "no", "description": "Trigger prototypes (same fields as `triggers[]`)."},
}


def _markdown_reference() -> str:
    lines = [
        "# zbx YAML Schema Reference",
        "",
        "This is the reference for all fields supported in zbx YAML template files.",
        "",
        "## Template",
        "",
        "```yaml",
        "template: my-template-id          # required",
        "name: My Template Display Name    # optional",
        "description: What this monitors   # optional",
        "groups:                           # optional",
        "  - Templates",
        "items: [...]",
        "triggers: [...]",
        "discovery_rules: [...]",
        "```",
        "",
        "---",
        "",
    ]

    sections: dict[str, list[tuple[str, str, str, str]]] = {}
    for field, meta in _FIELD_DOCS.items():
        section = field.split("[")[0] if "[" in field else "template"
        sections.setdefault(section, []).append(
            (field, meta["type"], meta["required"], meta["description"])
        )

    for section, rows in sections.items():
        lines.append(f"## {section.replace('_', ' ').title()}")
        lines.append("")
        lines.append("| Field | Type | Required | Description |")
        lines.append("|-------|------|----------|-------------|")
        for field, ftype, req, desc in rows:
            lines.append(f"| `{field}` | `{ftype}` | {req} | {desc} |")
        lines.append("")

    lines += [
        "---",
        "",
        "## Full example",
        "",
        "```yaml",
        "template: linux-observability",
        "name: Linux Observability",
        "description: CPU, memory and filesystem monitoring for Linux hosts",
        "groups:",
        "  - Templates/Operating Systems",
        "",
        "items:",
        "  - name: CPU usage",
        "    key: system.cpu.util",
        "    interval: 60s",
        "    value_type: float",
        "    units: '%'",
        "    tags:",
        "      - tag: component",
        "        value: cpu",
        "",
        "  - name: Memory usage",
        "    key: vm.memory.util",
        "    interval: 60s",
        "    units: '%'",
        "",
        "triggers:",
        "  - name: High CPU usage",
        "    expression: avg(/linux-observability/system.cpu.util,5m)>80",
        "    severity: high",
        "    tags:",
        "      - tag: scope",
        "        value: performance",
        "",
        "discovery_rules:",
        "  - name: Filesystem discovery",
        "    key: vfs.fs.discovery",
        "    interval: 1h",
        "    filter:",
        "      - macro: '{#FSTYPE}'",
        "        value: ext4|xfs",
        "    item_prototypes:",
        "      - name: 'Free space on {#FSNAME}'",
        "        key: 'vfs.fs.size[{#FSNAME},free]'",
        "        interval: 60s",
        "        value_type: int",
        "        units: B",
        "    trigger_prototypes:",
        "      - name: 'Low disk space on {#FSNAME}'",
        "        expression: 'last(/linux-observability/vfs.fs.size[{#FSNAME},pfree])<10'",
        "        severity: warning",
        "```",
    ]
    return "\n".join(lines)
