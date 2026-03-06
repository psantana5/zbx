"""zbx export — export Zabbix templates to YAML files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from zbx import formatter
from zbx.config_loader import ConfigLoader
from zbx.models import (
    DiscoveryRule,
    HostMacro,
    Item,
    ItemPrototype,
    ItemType,
    ItemValueType,
    LLDFilter,
    LLDFilterCondition,
    LLDFilterConditionOperator,
    LLDFilterEvalType,
    Tag,
    Template,
    Trigger,
    TriggerPrototype,
    TriggerSeverity,
)
from zbx.zabbix_client import ZabbixAPIError, ZabbixClient

console = Console()
app = typer.Typer()


def _slug(name: str) -> str:
    """Convert a template name to a safe filename slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def export_cmd(
    name: Optional[str] = typer.Argument(None, help="Template name or partial name to search for."),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write YAML to this file (single export). Prints to stdout if omitted.",
    ),
    all_templates: bool = typer.Option(
        False,
        "--all",
        help="Export every template from Zabbix to configs/templates/<slug>.yaml",
    ),
    out_dir: Path = typer.Option(
        Path("configs/templates"),
        "--out-dir",
        "-d",
        help="Output directory for --all (default: configs/templates).",
    ),
    env_file: Path = typer.Option(
        Path(".env"), "--env-file", "-e", help="Path to .env file with Zabbix credentials."
    ),
) -> None:
    """Export a Zabbix template (or every template) to YAML format."""
    if not all_templates and name is None:
        formatter.print_error("Provide a template name or use --all to export every template.")
        raise typer.Exit(1)

    loader = ConfigLoader()
    try:
        settings = loader.load_settings(env_file)
    except EnvironmentError as exc:
        formatter.print_error(str(exc))
        raise typer.Exit(1) from exc

    try:
        with ZabbixClient(settings) as client:
            if all_templates:
                _export_all(client, out_dir)
                return

            # ── single-template export ──────────────────────────────────────
            matches = client.find_templates(name)  # type: ignore[arg-type]

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
        console.print(f"[green]✓ Exported '{template.template}' → {output}[/green]")
    else:
        print(yaml_text, end="")


def _export_all(client: ZabbixClient, out_dir: Path) -> None:
    """Fetch and export every template; write one YAML per template."""
    all_tpls = client.get_all_templates()
    if not all_tpls:
        console.print("[yellow]No templates found in Zabbix.[/yellow]")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    ok: list[str] = []
    errors: list[tuple[str, str]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Exporting templates…", total=len(all_tpls))
        for tpl in all_tpls:
            progress.update(task, description=f"Exporting {tpl['host']!r}…")
            try:
                raw = client.export_template_raw(tpl["templateid"])
                if raw is None:
                    errors.append((tpl["host"], "empty response"))
                    continue
                template = _raw_to_template(raw)
                yaml_text = _template_to_yaml(template)
                dest = out_dir / f"{_slug(tpl['host'])}.yaml"
                dest.write_text(yaml_text)
                ok.append(tpl["host"])
            except Exception as exc:  # noqa: BLE001
                errors.append((tpl["host"], str(exc)))
            finally:
                progress.advance(task)

    # Summary table
    table = Table(title="Export summary", show_header=True)
    table.add_column("Status", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("[green]Exported[/green]", str(len(ok)))
    if errors:
        table.add_row("[red]Errors[/red]", str(len(errors)))
    console.print(table)

    if errors:
        console.print("[red]Errors:[/red]")
        for name, reason in errors:
            console.print(f"  • {name}: {reason}")
    else:
        console.print(f"[green]All {len(ok)} templates exported to {out_dir}/[/green]")


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _raw_to_template(raw: dict) -> Template:  # type: ignore[type-arg]
    # Build a map from itemid → key_ for resolving master_itemid references
    itemid_to_key: dict[str, str] = {}
    for i in raw.get("items", []):
        if i.get("itemid") and i.get("key_"):
            itemid_to_key[i["itemid"]] = i["key_"]

    items = [
        Item(
            name=i["name"],
            key=i["key_"],
            interval=i.get("delay", "60s"),
            type=ItemType.from_zabbix_id(int(i.get("type", 0))),
            value_type=ItemValueType.from_zabbix_id(int(i.get("value_type", 0))),
            units=i.get("units", ""),
            description=i.get("description", ""),
            params=i.get("params", ""),
            history=i.get("history", "90d"),
            trends=i.get("trends", "365d"),
            enabled=i.get("status", "0") == "0",
            master_item_key=itemid_to_key.get(i["master_itemid"]) if i.get("master_itemid") and i["master_itemid"] != "0" else None,
            tags=[Tag(tag=t["tag"], value=t.get("value", "")) for t in i.get("tags", [])],
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
            recovery_expression=t.get("recovery_expression", ""),
            description=t.get("comments", ""),
            enabled=t.get("status", "0") == "0",
            tags=[Tag(tag=tg["tag"], value=tg.get("value", "")) for tg in t.get("tags", [])],
        )
        for t in raw.get("triggers", [])
    ]

    discovery_rules = []
    for rule in raw.get("discoveryRules", []):
        # Build itemid→key map for resolving master_itemid within this rule's prototypes
        proto_itemid_to_key: dict[str, str] = {
            p["itemid"]: p["key_"]
            for p in rule.get("itemPrototypes", [])
            if p.get("itemid") and p.get("key_")
        }
        prototypes = [
            ItemPrototype(
                name=p["name"],
                key=p["key_"],
                interval=p.get("delay", "60s"),
                type=ItemType.from_zabbix_id(int(p.get("type", 0))),
                value_type=ItemValueType.from_zabbix_id(int(p.get("value_type", 0))),
                units=p.get("units", ""),
                params=p.get("params", ""),
                master_item_key=proto_itemid_to_key.get(p["master_itemid"]) if p.get("master_itemid") and p["master_itemid"] != "0" else None,
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
        raw_filter = rule.get("filter", {})
        lld_filter: Optional[LLDFilter] = None
        if raw_filter and raw_filter.get("conditions"):
            conditions = [
                LLDFilterCondition(
                    macro=c["macro"],
                    value=c.get("value", ""),
                    operator=LLDFilterConditionOperator.from_zabbix_id(int(c.get("operator", 8))),
                )
                for c in raw_filter["conditions"]
            ]
            lld_filter = LLDFilter(
                evaltype=LLDFilterEvalType.from_zabbix_id(int(raw_filter.get("evaltype", 0))),
                conditions=conditions,
            )
        # Resolve master_itemid for dependent discovery rules
        rule_master_key: Optional[str] = None
        master_iid = rule.get("master_itemid")
        if master_iid and master_iid != "0":
            rule_master_key = itemid_to_key.get(master_iid)
        discovery_rules.append(
            DiscoveryRule(
                name=rule["name"],
                key=rule["key_"],
                interval=rule.get("delay", "1h"),
                type=ItemType.from_zabbix_id(int(rule.get("type", 0))),
                master_item_key=rule_master_key,
                filter=lld_filter,
                item_prototypes=prototypes,
                trigger_prototypes=trig_protos,
            )
        )

    groups = [g["name"] for g in raw.get("groups", [{"name": "Templates"}])]

    macros = [
        HostMacro(
            macro=m["macro"],
            value=m.get("value", ""),
            description=m.get("description", ""),
        )
        for m in raw.get("macros", [])
    ]

    return Template(
        template=raw["host"],
        name=raw.get("name") if raw.get("name") != raw["host"] else None,
        description=raw.get("description", ""),
        groups=groups,
        macros=macros,
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
        if item.master_item_key:
            d["master_item_key"] = item.master_item_key
        if item.params:
            d["params"] = item.params
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
        if item.tags:
            d["tags"] = [{"tag": t.tag, "value": t.value} for t in item.tags]
        return d

    def _trigger_dict(t: Trigger) -> dict:  # type: ignore[type-arg]
        d: dict = {"name": t.name, "expression": t.expression, "severity": t.severity.value}  # type: ignore[type-arg]
        if t.recovery_expression:
            d["recovery_expression"] = t.recovery_expression
        if t.description:
            d["description"] = t.description
        if not t.enabled:
            d["enabled"] = False
        if t.tags:
            d["tags"] = [{"tag": tg.tag, "value": tg.value} for tg in t.tags]
        return d

    def _proto_dict(p: ItemPrototype) -> dict:  # type: ignore[type-arg]
        d: dict = {"name": p.name, "key": p.key, "interval": p.interval}  # type: ignore[type-arg]
        if p.type != ItemType.zabbix_agent:
            d["type"] = p.type.value
        if p.master_item_key:
            d["master_item_key"] = p.master_item_key
        if p.params:
            d["params"] = p.params
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
        if rule.type != ItemType.zabbix_agent:
            d["type"] = rule.type.value
        if rule.master_item_key:
            d["master_item_key"] = rule.master_item_key
        if rule.description:
            d["description"] = rule.description
        if rule.filter and rule.filter.conditions:
            d["filter"] = [
                {"macro": c.macro, "value": c.value} for c in rule.filter.conditions
            ]
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
    if template.macros:
        doc["macros"] = [
            {"macro": m.macro, "value": m.value, **({"description": m.description} if m.description else {})}
            for m in template.macros
        ]
    if template.items:
        doc["items"] = [_item_dict(i) for i in template.items]
    if template.triggers:
        doc["triggers"] = [_trigger_dict(t) for t in template.triggers]
    if template.discovery_rules:
        doc["discovery_rules"] = [_rule_dict(r) for r in template.discovery_rules]

    return yaml.dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)
