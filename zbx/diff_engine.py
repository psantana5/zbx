"""Compute the difference between desired YAML state and current Zabbix state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from zbx.models import (
    DiscoveryRule,
    Item,
    ItemType,
    ItemValueType,
    LLDFilter,
    LLDFilterConditionOperator,
    Template,
    Trigger,
    TriggerSeverity,
)


class ChangeType(str, Enum):
    ADD = "add"
    MODIFY = "modify"
    REMOVE = "remove"
    UNCHANGED = "unchanged"


@dataclass
class FieldChange:
    """A single changed field: old_value → new_value."""

    field: str
    old_value: Any
    new_value: Any


@dataclass
class ResourceChange:
    """A change to a single resource (item / trigger / discovery_rule)."""

    type: ChangeType
    resource_type: str  # "item" | "trigger" | "discovery_rule"
    name: str
    key: str | None = None
    resource_id: str | None = None
    field_changes: list[FieldChange] = field(default_factory=list)


@dataclass
class TemplateDiff:
    """Full diff for one template."""

    template_name: str
    template_change: ChangeType
    template_id: str | None
    field_changes: list[FieldChange] = field(default_factory=list)
    resource_changes: list[ResourceChange] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        if self.template_change in (ChangeType.ADD, ChangeType.REMOVE):
            return True
        return bool(self.field_changes) or any(
            r.type != ChangeType.UNCHANGED for r in self.resource_changes
        )

    @property
    def summary(self) -> dict[str, int]:
        counts = {ChangeType.ADD: 0, ChangeType.MODIFY: 0, ChangeType.REMOVE: 0}
        if self.template_change == ChangeType.ADD:
            counts[ChangeType.ADD] += 1
        elif self.template_change == ChangeType.MODIFY and self.field_changes:
            # Template-level field changes (name, description) count as 1 modify
            counts[ChangeType.MODIFY] += 1
        for rc in self.resource_changes:
            if rc.type in counts:
                counts[rc.type] += 1
        return {k.value: v for k, v in counts.items()}


class DiffEngine:
    """Compares a desired :class:`~zbx.models.Template` against the raw API response."""

    def compute_diff(
        self, desired: Template, current: dict[str, Any] | None
    ) -> TemplateDiff:
        if current is None:
            _, warnings = self._diff_discovery_rules(desired.discovery_rules, [])
            return TemplateDiff(
                template_name=desired.template,
                template_change=ChangeType.ADD,
                template_id=None,
                resource_changes=self._all_resources_as_add(desired),
                warnings=warnings,
            )

        template_id = str(current["templateid"])
        field_changes = self._diff_template_fields(desired, current)
        resource_changes: list[ResourceChange] = []

        resource_changes.extend(self._diff_items(desired.items, current.get("items", [])))
        resource_changes.extend(
            self._diff_triggers(desired.triggers, current.get("triggers", []))
        )
        rule_changes, warnings = self._diff_discovery_rules(
            desired.discovery_rules, current.get("discoveryRules", [])
        )
        resource_changes.extend(rule_changes)

        has_any_change = bool(field_changes) or any(
            r.type != ChangeType.UNCHANGED for r in resource_changes
        )
        template_change = ChangeType.MODIFY if has_any_change else ChangeType.UNCHANGED

        return TemplateDiff(
            template_name=desired.template,
            template_change=template_change,
            template_id=template_id,
            field_changes=field_changes,
            resource_changes=resource_changes,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Template-level fields
    # ------------------------------------------------------------------

    def _diff_template_fields(
        self, desired: Template, current: dict[str, Any]
    ) -> list[FieldChange]:
        changes: list[FieldChange] = []
        if desired.display_name != current.get("name", desired.template):
            changes.append(FieldChange("name", current.get("name"), desired.display_name))
        if desired.description != current.get("description", ""):
            changes.append(
                FieldChange("description", current.get("description", ""), desired.description)
            )
        return changes

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

    def _diff_items(
        self,
        desired_items: list[Item],
        current_items: list[dict[str, Any]],
    ) -> list[ResourceChange]:
        current_by_key = {i["key_"]: i for i in current_items}
        desired_keys = {i.key for i in desired_items}
        changes: list[ResourceChange] = []

        for item in desired_items:
            cur = current_by_key.get(item.key)
            if cur is None:
                changes.append(
                    ResourceChange(
                        type=ChangeType.ADD,
                        resource_type="item",
                        name=item.name,
                        key=item.key,
                    )
                )
            else:
                field_changes = self._diff_item_fields(item, cur)
                changes.append(
                    ResourceChange(
                        type=ChangeType.MODIFY if field_changes else ChangeType.UNCHANGED,
                        resource_type="item",
                        name=item.name,
                        key=item.key,
                        resource_id=str(cur["itemid"]),
                        field_changes=field_changes,
                    )
                )

        for key, cur in current_by_key.items():
            if key not in desired_keys:
                changes.append(
                    ResourceChange(
                        type=ChangeType.REMOVE,
                        resource_type="item",
                        name=cur["name"],
                        key=key,
                        resource_id=str(cur["itemid"]),
                    )
                )
        return changes

    @staticmethod
    def _tags_sig(tags: list[Any]) -> str:
        """Canonical string for a tag list for comparison."""
        return "|".join(sorted(f"{t.tag}={t.value}" for t in tags))

    @staticmethod
    def _tags_sig_from_raw(tags: list[dict[str, str]]) -> str:
        return "|".join(sorted(f"{t.get('tag','')}={t.get('value','')}" for t in tags))

    def _diff_item_fields(self, desired: Item, current: dict[str, Any]) -> list[FieldChange]:
        changes: list[FieldChange] = []
        _chk = self._chk
        _chk(changes, "name", current.get("name"), desired.name)
        _chk(changes, "interval", current.get("delay"), desired.interval)
        _chk(
            changes,
            "type",
            ItemType.from_zabbix_id(int(current.get("type", 0))).value,
            desired.type.value,
        )
        _chk(
            changes,
            "value_type",
            ItemValueType.from_zabbix_id(int(current.get("value_type", 0))).value,
            desired.value_type.value,
        )
        _chk(changes, "units", current.get("units", ""), desired.units)
        _chk(changes, "history", current.get("history", "90d"), desired.history)
        # char/log/text items can't store trends — deployer forces trends=0 for these types,
        # so we must apply the same logic here to avoid a perpetual false diff.
        _no_trends = {ItemValueType.char, ItemValueType.log, ItemValueType.text}
        effective_trends = "0" if desired.value_type in _no_trends else desired.trends
        _chk(changes, "trends", current.get("trends", "365d"), effective_trends)
        # Compare tags
        _chk(
            changes, "tags",
            self._tags_sig_from_raw(current.get("tags", [])),
            self._tags_sig(desired.tags),
        )
        return changes

    # ------------------------------------------------------------------
    # Triggers
    # ------------------------------------------------------------------

    def _diff_triggers(
        self,
        desired_triggers: list[Trigger],
        current_triggers: list[dict[str, Any]],
    ) -> list[ResourceChange]:
        current_by_name = {t["description"]: t for t in current_triggers}
        desired_names = {t.name for t in desired_triggers}
        changes: list[ResourceChange] = []

        for trigger in desired_triggers:
            cur = current_by_name.get(trigger.name)
            if cur is None:
                changes.append(
                    ResourceChange(
                        type=ChangeType.ADD,
                        resource_type="trigger",
                        name=trigger.name,
                    )
                )
            else:
                field_changes = self._diff_trigger_fields(trigger, cur)
                changes.append(
                    ResourceChange(
                        type=ChangeType.MODIFY if field_changes else ChangeType.UNCHANGED,
                        resource_type="trigger",
                        name=trigger.name,
                        resource_id=str(cur["triggerid"]),
                        field_changes=field_changes,
                    )
                )

        for name, cur in current_by_name.items():
            if name not in desired_names:
                changes.append(
                    ResourceChange(
                        type=ChangeType.REMOVE,
                        resource_type="trigger",
                        name=name,
                        resource_id=str(cur["triggerid"]),
                    )
                )
        return changes

    def _diff_trigger_fields(
        self, desired: Trigger, current: dict[str, Any]
    ) -> list[FieldChange]:
        changes: list[FieldChange] = []
        _chk = self._chk
        _chk(changes, "expression", current.get("expression", ""), desired.expression)
        _chk(
            changes,
            "severity",
            TriggerSeverity.from_zabbix_id(int(current.get("priority", 0))).value,
            desired.severity.value,
        )
        _chk(changes, "recovery_expression", current.get("recovery_expression", ""), desired.recovery_expression)
        _chk(changes, "description", current.get("comments", ""), desired.description)
        current_enabled = current.get("status", "0") == "0"
        _chk(changes, "enabled", current_enabled, desired.enabled)
        _chk(
            changes, "tags",
            self._tags_sig_from_raw(current.get("tags", [])),
            self._tags_sig(desired.tags),
        )
        return changes

    # ------------------------------------------------------------------
    # Discovery rules
    # ------------------------------------------------------------------

    def _diff_discovery_rules(
        self,
        desired_rules: list[DiscoveryRule],
        current_rules: list[dict[str, Any]],
    ) -> tuple[list[ResourceChange], list[str]]:
        current_by_key = {r["key_"]: r for r in current_rules}
        desired_keys = {r.key for r in desired_rules}
        changes: list[ResourceChange] = []
        warnings: list[str] = []

        for rule in desired_rules:
            # Validate dependent item prototype master references
            non_dep_keys = {
                p.key for p in rule.item_prototypes
                if p.type != ItemType.dependent
            }
            for proto in rule.item_prototypes:
                if proto.type == ItemType.dependent and proto.master_item_key:
                    if proto.master_item_key not in non_dep_keys:
                        warnings.append(
                            f"'{rule.name}': dependent item '{proto.name}' references "
                            f"master_item_key '{proto.master_item_key}' which does not "
                            f"exist in the same discovery rule — it will be skipped during apply."
                        )

            cur = current_by_key.get(rule.key)
            if cur is None:
                changes.append(
                    ResourceChange(
                        type=ChangeType.ADD,
                        resource_type="discovery_rule",
                        name=rule.name,
                        key=rule.key,
                    )
                )
            else:
                field_changes: list[FieldChange] = []
                self._chk(field_changes, "name", cur.get("name"), rule.name)
                self._chk(field_changes, "interval", cur.get("delay"), rule.interval)
                # Compare filter conditions (serialize to stable string for comparison)
                desired_filter = self._filter_sig(rule.filter)
                current_filter_raw = cur.get("filter", {})
                current_filter = self._filter_sig_from_raw(current_filter_raw)
                self._chk(field_changes, "filter", current_filter, desired_filter)
                # Compare item prototype key sets
                current_proto_keys = {p["key_"] for p in cur.get("itemPrototypes", [])}
                desired_proto_keys = {p.key for p in rule.item_prototypes}
                if current_proto_keys != desired_proto_keys:
                    new_keys = desired_proto_keys - current_proto_keys
                    removed_keys = current_proto_keys - desired_proto_keys
                    sig_old = ",".join(sorted(current_proto_keys))
                    sig_new = ",".join(sorted(desired_proto_keys))
                    if new_keys or removed_keys:
                        field_changes.append(FieldChange("item_prototypes", sig_old, sig_new))
                # Compare trigger prototype name sets
                current_tp_names = {t["description"] for t in cur.get("triggerPrototypes", [])}
                desired_tp_names = {t.name for t in rule.trigger_prototypes}
                if current_tp_names != desired_tp_names:
                    sig_old = ",".join(sorted(current_tp_names))
                    sig_new = ",".join(sorted(desired_tp_names))
                    field_changes.append(FieldChange("trigger_prototypes", sig_old, sig_new))
                changes.append(
                    ResourceChange(
                        type=ChangeType.MODIFY if field_changes else ChangeType.UNCHANGED,
                        resource_type="discovery_rule",
                        name=rule.name,
                        key=rule.key,
                        resource_id=str(cur["itemid"]),
                        field_changes=field_changes,
                    )
                )

        for key, cur in current_by_key.items():
            if key not in desired_keys:
                changes.append(
                    ResourceChange(
                        type=ChangeType.REMOVE,
                        resource_type="discovery_rule",
                        name=cur["name"],
                        key=key,
                        resource_id=str(cur["itemid"]),
                    )
                )
        return changes, warnings

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_sig(f: LLDFilter | None) -> str:
        """Produce a canonical string representation of a desired LLD filter."""
        if f is None or not f.conditions:
            return ""
        parts = sorted(
            f"{c.macro}:{c.operator.zabbix_id}:{c.value}"
            for c in f.conditions
        )
        return f"{f.evaltype.zabbix_id}|{'|'.join(parts)}"

    @staticmethod
    def _filter_sig_from_raw(raw: dict[str, Any] | None) -> str:
        """Produce the same canonical string from a raw Zabbix API filter dict."""
        if not raw or not raw.get("conditions"):
            return ""
        parts = sorted(
            f"{c['macro']}:{c['operator']}:{c['value']}"
            for c in raw["conditions"]
        )
        return f"{raw.get('evaltype', '0')}|{'|'.join(parts)}"

    @staticmethod
    def _chk(
        changes: list[FieldChange], field_name: str, old: Any, new: Any
    ) -> None:
        if str(old) != str(new):
            changes.append(FieldChange(field_name, old, new))

    def _all_resources_as_add(self, desired: Template) -> list[ResourceChange]:
        out: list[ResourceChange] = []
        for item in desired.items:
            out.append(
                ResourceChange(
                    type=ChangeType.ADD, resource_type="item", name=item.name, key=item.key
                )
            )
        for trigger in desired.triggers:
            out.append(
                ResourceChange(type=ChangeType.ADD, resource_type="trigger", name=trigger.name)
            )
        for rule in desired.discovery_rules:
            out.append(
                ResourceChange(
                    type=ChangeType.ADD,
                    resource_type="discovery_rule",
                    name=rule.name,
                    key=rule.key,
                )
            )
        return out
