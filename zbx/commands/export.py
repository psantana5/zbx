"""zbx export — export Zabbix templates to YAML files."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console

from zbx import formatter
from zbx.config_loader import ConfigLoader
from zbx.models import (
    DiscoveryRule,
    Item,
    ItemPrototype,
    ItemType,
    ItemValueType,
    Template,
    Trigger,
    TriggerPrototype,
    TriggerSeverity,
)
from zbx.zabbix_client import ZabbixAPIError, ZabbixClient

console = Console()
app = typer.Typer()


def export_cmd(
    name: str = typer.Argument(..., help="Template name or partial name to search for."),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write YAML to this file. Prints to stdout if omitted.",
    ),
    env_file: Path = typer.Option(
        Path(".env"), "--env-file", "-e", help="Path to .env file with Zabbix credentials."
    ),
) -> None:
    """Export a Zabbix template to YAML format (for migrating to Git)."""
    loader = ConfigLoader()

    try:
        settings = loader.load_settings(env_file)
    except EnvironmentError as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        with ZabbixClient(settings) as client:
            matches = client.find_templates(name)

            if not matches:
                formatter.print_error(f"No template found matching '{name}'.")
                raise typer.Exit(1)

            if len(matches) > 1:
                console.print(f"[yellow]Multiple templates match '{name}':[/yellow]")
                for m in matches:
                    console.print(f"  • {m['host']}  [dim](id={m['templateid']})[/dim]")
                formatter.print_error(
                    "Be more specific. Use the exact template name shown above."
                )
                raise typer.Exit(1)

            raw = client.export_template_raw(matches[0]["templateid"])
    except ZabbixAPIError as exc:
        formatter.print_error(f"Zabbix API: {exc}")
        raise typer.Exit(1) from exc

    if raw is None:
        formatter.print_error("Failed to fetch template data.")
        raise typer.Exit(1)

    template = _raw_to_template(raw)
    yaml_text = _template_to_yaml(template)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(yaml_text)
        console.print(f"[green]ok Exported '{template.template}' to {output}[/green]")
    else:
        console.print(yaml_text)


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _raw_to_template(raw: dict) -> Template:  # type: ignore[type-arg]
    items = [
        Item(
            name=i["name"],
            key=i["key_"],
            interval=i.get("delay", "60s"),
            type=ItemType.from_zabbix_id(int(i.get("type", 0))),
            value_type=ItemValueType.from_zabbix_id(int(i.get("value_type", 0))),
            units=i.get("units", ""),
            description=i.get("description", ""),
            history=i.get("history", "90d"),
            trends=i.get("trends", "365d"),
            enabled=i.get("status", "0") == "0",
        )
        for i in raw.get("items", [])
        # Exclude items that belong to discovery rule prototypes
        if not i.get("discoveryRule")
    ]

    triggers = [
        Trigger(
            name=t["description"],
            expression=t["expression"],
            severity=TriggerSeverity.from_zabbix_id(int(t.get("priority", 0))),
            description=t.get("comments", ""),
            enabled=t.get("status", "0") == "0",
        )
        for t in raw.get("triggers", [])
    ]

    discovery_rules = []
    for rule in raw.get("discoveryRules", []):
        prototypes = [
            ItemPrototype(
                name=p["name"],
                key=p["key_"],
                interval=p.get("delay", "60s"),
                type=ItemType.from_zabbix_id(int(p.get("type", 0))),
                value_type=ItemValueType.from_zabbix_id(int(p.get("value_type", 0))),
                units=p.get("units", ""),
            )
            for p in rule.get("itemPrototypes", [])
        ]
        trig_protos = [
            TriggerPrototype(
                name=tp["description"],
                expression=tp["expression"],
                severity=TriggerSeverity.from_zabbix_id(int(tp.get("priority", 0))),
                recovery_expression=tp.get("recovery_expression", ""),
                description=tp.get("comments", ""),
                allow_manual_close=tp.get("manual_close", "0") == "1",
                enabled=tp.get("status", "0") == "0",
            )
            for tp in rule.get("triggerPrototypes", [])
        ]
        discovery_rules.append(
            DiscoveryRule(
                name=rule["name"],
                key=rule["key_"],
                interval=rule.get("delay", "1h"),
                type=ItemType.from_zabbix_id(int(rule.get("type", 0))),
                item_prototypes=prototypes,
                trigger_prototypes=trig_protos,
            )
        )

    groups = [g["name"] for g in raw.get("groups", [{"name": "Templates"}])]

    return Template(
        template=raw["host"],
        name=raw.get("name") if raw.get("name") != raw["host"] else None,
        description=raw.get("description", ""),
        groups=groups,
        items=items,
        triggers=triggers,
        discovery_rules=discovery_rules,
    )


def _template_to_yaml(template: Template) -> str:
    """Serialize a Template to clean YAML text."""

    def _item_dict(item: Item) -> dict:  # type: ignore[type-arg]
        d: dict = {"name": item.name, "key": item.key, "interval": item.interval}  # type: ignore[type-arg]
        if item.type != ItemType.zabbix_agent:
            d["type"] = item.type.value
        if item.value_type.value != "float":
            d["value_type"] = item.value_type.value
        if item.units:
            d["units"] = item.units
        if item.description:
            d["description"] = item.description
        if item.history != "90d":
            d["history"] = item.history
        if item.trends != "365d":
            d["trends"] = item.trends
        if not item.enabled:
            d["enabled"] = False
        return d

    def _trigger_dict(t: Trigger) -> dict:  # type: ignore[type-arg]
        d: dict = {"name": t.name, "expression": t.expression, "severity": t.severity.value}  # type: ignore[type-arg]
        if t.description:
            d["description"] = t.description
        if not t.enabled:
            d["enabled"] = False
        return d

    def _proto_dict(p: ItemPrototype) -> dict:  # type: ignore[type-arg]
        d: dict = {"name": p.name, "key": p.key, "interval": p.interval}  # type: ignore[type-arg]
        if p.type != ItemType.zabbix_agent:
            d["type"] = p.type.value
        if p.value_type.value != "float":
            d["value_type"] = p.value_type.value
        if p.units:
            d["units"] = p.units
        return d

    def _trig_proto_dict(tp: TriggerPrototype) -> dict:  # type: ignore[type-arg]
        d: dict = {"name": tp.name, "expression": tp.expression, "severity": tp.severity.value}  # type: ignore[type-arg]
        if tp.recovery_expression:
            d["recovery_expression"] = tp.recovery_expression
        if tp.description:
            d["description"] = tp.description
        if tp.allow_manual_close:
            d["allow_manual_close"] = True
        if not tp.enabled:
            d["enabled"] = False
        return d

    def _rule_dict(rule: DiscoveryRule) -> dict:  # type: ignore[type-arg]
        d: dict = {"name": rule.name, "key": rule.key, "interval": rule.interval}  # type: ignore[type-arg]
        if rule.description:
            d["description"] = rule.description
        if rule.item_prototypes:
            d["item_prototypes"] = [_proto_dict(p) for p in rule.item_prototypes]
        if rule.trigger_prototypes:
            d["trigger_prototypes"] = [_trig_proto_dict(tp) for tp in rule.trigger_prototypes]
        return d

    doc: dict = {"template": template.template}  # type: ignore[type-arg]
    if template.name:
        doc["name"] = template.name
    if template.description:
        doc["description"] = template.description
    doc["groups"] = template.groups
    if template.items:
        doc["items"] = [_item_dict(i) for i in template.items]
    if template.triggers:
        doc["triggers"] = [_trigger_dict(t) for t in template.triggers]
    if template.discovery_rules:
        doc["discovery_rules"] = [_rule_dict(r) for r in template.discovery_rules]

    return yaml.dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)
