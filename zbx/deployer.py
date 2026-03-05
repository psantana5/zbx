"""Apply desired Template state to a Zabbix server."""

from __future__ import annotations

import logging
from typing import Any

from zbx.diff_engine import ChangeType, DiffEngine, TemplateDiff
from zbx.models import DiscoveryRule, Item, ItemPrototype, ItemType, Template, Trigger, TriggerPrototype
from zbx.zabbix_client import ZabbixClient

logger = logging.getLogger(__name__)


class Deployer:
    """
    Computes diffs and applies them to Zabbix.

    When *dry_run* is ``True``, diffs are computed and returned but no
    write operations are sent to the API.
    """

    def __init__(self, client: ZabbixClient, dry_run: bool = False) -> None:
        self._client = client
        self._dry_run = dry_run
        self._diff_engine = DiffEngine()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(self, template: Template) -> TemplateDiff:
        """Compute the diff without making any changes."""
        current = self._client.get_template_full(template.template)
        return self._diff_engine.compute_diff(template, current)

    def apply(self, template: Template) -> TemplateDiff:
        """Apply *template* to Zabbix; honour the dry_run flag."""
        diff = self.plan(template)

        if not diff.has_changes:
            logger.info("Template '%s': already up-to-date.", template.template)
            return diff

        if self._dry_run:
            logger.info("Dry-run — no changes applied for '%s'.", template.template)
            return diff

        if diff.template_change == ChangeType.ADD:
            self._create_template(template)
        else:
            self._update_template(template, diff)

        return diff

    # ------------------------------------------------------------------
    # Template-level operations
    # ------------------------------------------------------------------

    def _create_template(self, template: Template) -> None:
        group_ids = [self._client.ensure_templategroup(g) for g in template.groups]
        templateid = self._client.create_template(
            host=template.template,
            name=template.display_name,
            description=template.description,
            group_ids=group_ids,
        )
        logger.info("Created template '%s' (id=%s).", template.template, templateid)

        for item in template.items:
            self._create_item(templateid, item)
        for trigger in template.triggers:
            self._create_trigger(trigger)
        for rule in template.discovery_rules:
            self._create_discovery_rule(templateid, rule)

    def _update_template(self, template: Template, diff: TemplateDiff) -> None:
        assert diff.template_id is not None
        templateid = diff.template_id

        if diff.field_changes:
            update: dict[str, Any] = {}
            for fc in diff.field_changes:
                update[fc.field] = fc.new_value
            self._client.update_template(templateid, **update)
            logger.info("Updated template fields for '%s'.", template.template)

        for rc in diff.resource_changes:
            if rc.type == ChangeType.UNCHANGED:
                continue

            if rc.resource_type == "item":
                self._handle_item_change(rc, template, templateid)
            elif rc.resource_type == "trigger":
                self._handle_trigger_change(rc, template)
            elif rc.resource_type == "discovery_rule":
                self._handle_discovery_rule_change(rc, template, templateid)

    # ------------------------------------------------------------------
    # Item operations
    # ------------------------------------------------------------------

    def _handle_item_change(
        self, rc: Any, template: Template, templateid: str
    ) -> None:
        if rc.type == ChangeType.ADD:
            item = self._find_item(template, rc.key)
            self._create_item(templateid, item)
        elif rc.type == ChangeType.MODIFY:
            item = self._find_item(template, rc.key)
            self._update_item(rc.resource_id, item)
        elif rc.type == ChangeType.REMOVE:
            logger.warning(
                "Item '%s' (%s) exists in Zabbix but is absent from config — skipping removal "
                "(remove manually if intended).",
                rc.name,
                rc.key,
            )

    def _create_item(self, templateid: str, item: Item) -> None:
        data: dict[str, Any] = {
            "name": item.name,
            "key_": item.key,
            "delay": item.interval,
            "type": item.type.zabbix_id,
            "value_type": item.value_type.zabbix_id,
            "units": item.units,
            "description": item.description,
            "history": item.history,
            "trends": item.trends,
            "status": 0 if item.enabled else 1,
        }
        if item.tags:
            data["tags"] = [{"tag": t.tag, "value": t.value} for t in item.tags]
        itemid = self._client.create_item(templateid, data)
        logger.info("  + item '%s' (%s) id=%s", item.name, item.key, itemid)

    def _update_item(self, itemid: str, item: Item) -> None:
        self._client.update_item(
            itemid,
            name=item.name,
            delay=item.interval,
            type=item.type.zabbix_id,
            value_type=item.value_type.zabbix_id,
            units=item.units,
            description=item.description,
            history=item.history,
            trends=item.trends,
            status=0 if item.enabled else 1,
        )
        logger.info("  ~ item '%s' (%s)", item.name, item.key)

    # ------------------------------------------------------------------
    # Trigger operations
    # ------------------------------------------------------------------

    def _handle_trigger_change(self, rc: Any, template: Template) -> None:
        if rc.type == ChangeType.ADD:
            trigger = self._find_trigger(template, rc.name)
            self._create_trigger(trigger)
        elif rc.type == ChangeType.MODIFY:
            trigger = self._find_trigger(template, rc.name)
            self._update_trigger(rc.resource_id, trigger)
        elif rc.type == ChangeType.REMOVE:
            logger.warning(
                "Trigger '%s' exists in Zabbix but is absent from config — skipping removal.",
                rc.name,
            )

    def _create_trigger(self, trigger: Trigger) -> None:
        data: dict[str, Any] = {
            "description": trigger.name,
            "expression": trigger.expression,
            "priority": trigger.severity.zabbix_id,
            "comments": trigger.description,
            "status": 0 if trigger.enabled else 1,
        }
        if trigger.tags:
            data["tags"] = [{"tag": t.tag, "value": t.value} for t in trigger.tags]
        triggerid = self._client.create_trigger(data)
        logger.info("  + trigger '%s' id=%s", trigger.name, triggerid)

    def _update_trigger(self, triggerid: str, trigger: Trigger) -> None:
        self._client.update_trigger(
            triggerid,
            description=trigger.name,
            expression=trigger.expression,
            priority=trigger.severity.zabbix_id,
            comments=trigger.description,
            status=0 if trigger.enabled else 1,
        )
        logger.info("  ~ trigger '%s'", trigger.name)

    # ------------------------------------------------------------------
    # Discovery rule operations
    # ------------------------------------------------------------------

    def _handle_discovery_rule_change(
        self, rc: Any, template: Template, templateid: str
    ) -> None:
        if rc.type == ChangeType.ADD:
            rule = self._find_rule(template, rc.key)
            self._create_discovery_rule(templateid, rule)
        elif rc.type == ChangeType.MODIFY:
            rule = self._find_rule(template, rc.key)
            self._update_discovery_rule(rc.resource_id, rule)
        elif rc.type == ChangeType.REMOVE:
            logger.warning(
                "Discovery rule '%s' exists in Zabbix but is absent from config — skipping.",
                rc.name,
            )

    def _create_discovery_rule(self, templateid: str, rule: DiscoveryRule) -> None:
        data: dict[str, Any] = {
            "name": rule.name,
            "key_": rule.key,
            "delay": rule.interval,
            "type": rule.type.zabbix_id,
            "description": rule.description,
        }
        ruleid = self._client.create_discovery_rule(templateid, data)
        logger.info("  + discovery rule '%s' id=%s", rule.name, ruleid)

        # Create non-dependent prototypes first so their IDs can be resolved
        key_to_itemid: dict[str, str] = {}
        for proto in rule.item_prototypes:
            if proto.type != ItemType.dependent:
                pid = self._create_item_prototype(ruleid, templateid, proto)
                key_to_itemid[proto.key] = pid

        # Now create dependent prototypes, injecting master_itemid
        for proto in rule.item_prototypes:
            if proto.type == ItemType.dependent:
                if proto.master_item_key and proto.master_item_key in key_to_itemid:
                    master_id = key_to_itemid[proto.master_item_key]
                    self._create_item_prototype(ruleid, templateid, proto, master_itemid=master_id)
                else:
                    logger.warning(
                        "  ⚠ Dependent prototype '%s' references unknown master_item_key '%s' — skipped.",
                        proto.name,
                        proto.master_item_key,
                    )

        for tp in rule.trigger_prototypes:
            self._create_trigger_prototype(tp)

    def _update_discovery_rule(self, ruleid: str, rule: DiscoveryRule) -> None:
        self._client.update_discovery_rule(
            ruleid,
            name=rule.name,
            delay=rule.interval,
            type=rule.type.zabbix_id,
        )
        logger.info("  ~ discovery rule '%s'", rule.name)

    def _create_item_prototype(
        self, ruleid: str, templateid: str, proto: ItemPrototype,
        master_itemid: str | None = None,
    ) -> str:
        data: dict[str, Any] = {
            "name": proto.name,
            "key_": proto.key,
            "delay": proto.interval if proto.type != ItemType.dependent else "0",
            "type": proto.type.zabbix_id,
            "value_type": proto.value_type.zabbix_id,
            "units": proto.units,
            "description": proto.description,
        }
        if master_itemid:
            data["master_itemid"] = master_itemid
        if proto.preprocessing:
            data["preprocessing"] = [
                {
                    "type": str(p.type.zabbix_id),
                    "params": p.params,
                    "error_handler": str(p.error_handler),
                    "error_handler_params": p.error_handler_params,
                }
                for p in proto.preprocessing
            ]
        itemid = self._client.create_item_prototype(ruleid, templateid, data)
        logger.info("    + prototype '%s' id=%s", proto.name, itemid)
        return itemid

    def _create_trigger_prototype(self, tp: TriggerPrototype) -> None:
        data: dict[str, Any] = {
            "description": tp.name,
            "expression": tp.expression,
            "priority": tp.severity.zabbix_id,
            "comments": tp.description,
            "status": 0 if tp.enabled else 1,
            "manual_close": 1 if tp.allow_manual_close else 0,
        }
        if tp.recovery_expression:
            data["recovery_mode"] = 1
            data["recovery_expression"] = tp.recovery_expression
        if tp.tags:
            data["tags"] = [{"tag": t.tag, "value": t.value} for t in tp.tags]
        triggerid = self._client.create_trigger_prototype(data)
        logger.info("    + trigger prototype '%s' id=%s", tp.name, triggerid)

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_item(template: Template, key: str | None) -> Item:
        item = next((i for i in template.items if i.key == key), None)
        if item is None:
            raise KeyError(f"Item with key '{key}' not found in template '{template.template}'")
        return item

    @staticmethod
    def _find_trigger(template: Template, name: str) -> Trigger:
        trigger = next((t for t in template.triggers if t.name == name), None)
        if trigger is None:
            raise KeyError(f"Trigger '{name}' not found in template '{template.template}'")
        return trigger

    @staticmethod
    def _find_rule(template: Template, key: str | None) -> DiscoveryRule:
        rule = next((r for r in template.discovery_rules if r.key == key), None)
        if rule is None:
            raise KeyError(
                f"Discovery rule with key '{key}' not found in template '{template.template}'"
            )
        return rule
