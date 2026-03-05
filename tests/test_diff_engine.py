"""Unit tests for DiffEngine — no live Zabbix connection."""

from __future__ import annotations

import pytest

from zbx.diff_engine import ChangeType, DiffEngine, TemplateDiff
from zbx.models import Item, ItemType, ItemValueType, Template, Trigger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_template(
    name: str = "T_Test",
    items: list[dict] | None = None,
    triggers: list[dict] | None = None,
) -> Template:
    return Template(
        template=name,
        items=[Item(**i) for i in (items or [])],
        triggers=[Trigger(**t) for t in (triggers or [])],
    )


def _make_current(
    template_id: str = "1001",
    name: str = "T_Test",
    description: str = "",
    items: list[dict] | None = None,
    triggers: list[dict] | None = None,
    discovery_rules: list[dict] | None = None,
) -> dict:
    """Build a fake Zabbix API template dict (what the real client would return)."""
    return {
        "templateid": template_id,
        "name": name,
        "description": description,
        "items": items or [],
        "triggers": triggers or [],
        "discoveryRules": discovery_rules or [],
    }


def _zabbix_item(
    itemid: str,
    key: str,
    name: str,
    delay: str = "60s",
    itype: int = 0,
    value_type: int = 0,
    units: str = "",
    history: str = "90d",
    trends: str = "365d",
    tags: list | None = None,
) -> dict:
    return {
        "itemid": itemid,
        "key_": key,
        "name": name,
        "delay": delay,
        "type": str(itype),
        "value_type": str(value_type),
        "units": units,
        "history": history,
        "trends": trends,
        "tags": tags or [],
    }


def _zabbix_trigger(
    triggerid: str,
    description: str,
    expression: str,
    priority: int = 3,
    recovery_expression: str = "",
    comments: str = "",
    status: str = "0",
    tags: list | None = None,
) -> dict:
    return {
        "triggerid": triggerid,
        "description": description,
        "expression": expression,
        "priority": str(priority),
        "recovery_expression": recovery_expression,
        "comments": comments,
        "status": status,
        "tags": tags or [],
    }


engine = DiffEngine()


# ---------------------------------------------------------------------------
# Template doesn't exist in Zabbix → all ADD
# ---------------------------------------------------------------------------

class TestTemplateNotInZabbix:
    def test_all_items_are_add(self):
        desired = _make_template(
            items=[
                {"name": "CPU", "key": "system.cpu.util"},
                {"name": "Mem", "key": "vm.memory.size"},
            ],
        )
        diff = engine.compute_diff(desired, current=None)
        assert diff.template_change == ChangeType.ADD
        item_changes = [r for r in diff.resource_changes if r.resource_type == "item"]
        assert all(r.type == ChangeType.ADD for r in item_changes)
        assert {r.key for r in item_changes} == {"system.cpu.util", "vm.memory.size"}

    def test_all_triggers_are_add(self):
        desired = _make_template(
            triggers=[
                {"name": "High CPU", "expression": "avg(/T_Test/system.cpu.util,5m)>90"},
                {"name": "No mem",   "expression": "last(/T_Test/vm.memory.size)<100M"},
            ],
        )
        diff = engine.compute_diff(desired, current=None)
        trigger_changes = [r for r in diff.resource_changes if r.resource_type == "trigger"]
        assert all(r.type == ChangeType.ADD for r in trigger_changes)
        assert len(trigger_changes) == 2

    def test_template_id_is_none(self):
        desired = _make_template()
        diff = engine.compute_diff(desired, current=None)
        assert diff.template_id is None


# ---------------------------------------------------------------------------
# Template is identical → everything UNCHANGED
# ---------------------------------------------------------------------------

class TestTemplateIdentical:
    def test_items_unchanged(self):
        desired = _make_template(
            items=[{"name": "CPU", "key": "system.cpu.util", "interval": "60s"}],
        )
        current = _make_current(
            items=[_zabbix_item("10", "system.cpu.util", "CPU", delay="60s")],
        )
        diff = engine.compute_diff(desired, current)
        item_changes = [r for r in diff.resource_changes if r.resource_type == "item"]
        assert all(r.type == ChangeType.UNCHANGED for r in item_changes)

    def test_triggers_unchanged(self):
        expr = "avg(/T_Test/system.cpu.util,5m)>90"
        desired = _make_template(
            triggers=[{"name": "High CPU", "expression": expr, "severity": "average"}],
        )
        current = _make_current(
            triggers=[_zabbix_trigger("20", "High CPU", expr, priority=3)],
        )
        diff = engine.compute_diff(desired, current)
        trigger_changes = [r for r in diff.resource_changes if r.resource_type == "trigger"]
        assert all(r.type == ChangeType.UNCHANGED for r in trigger_changes)

    def test_template_change_is_unchanged(self):
        desired = _make_template(
            items=[{"name": "CPU", "key": "system.cpu.util", "interval": "60s"}],
        )
        current = _make_current(
            name="T_Test",
            items=[_zabbix_item("10", "system.cpu.util", "CPU", delay="60s")],
        )
        diff = engine.compute_diff(desired, current)
        assert diff.template_change == ChangeType.UNCHANGED


# ---------------------------------------------------------------------------
# Item interval changes → that item MODIFY, others UNCHANGED
# ---------------------------------------------------------------------------

class TestItemIntervalChange:
    def test_changed_item_is_modify(self):
        desired = _make_template(
            items=[
                {"name": "CPU", "key": "system.cpu.util", "interval": "30s"},
                {"name": "Mem", "key": "vm.memory.size",  "interval": "60s"},
            ],
        )
        current = _make_current(
            items=[
                _zabbix_item("10", "system.cpu.util", "CPU", delay="60s"),
                _zabbix_item("11", "vm.memory.size",  "Mem", delay="60s"),
            ],
        )
        diff = engine.compute_diff(desired, current)
        by_key = {r.key: r for r in diff.resource_changes if r.resource_type == "item"}
        assert by_key["system.cpu.util"].type == ChangeType.MODIFY
        assert by_key["vm.memory.size"].type  == ChangeType.UNCHANGED

    def test_modify_has_field_change_for_interval(self):
        desired = _make_template(
            items=[{"name": "CPU", "key": "system.cpu.util", "interval": "30s"}],
        )
        current = _make_current(
            items=[_zabbix_item("10", "system.cpu.util", "CPU", delay="60s")],
        )
        diff = engine.compute_diff(desired, current)
        cpu_change = next(r for r in diff.resource_changes if r.key == "system.cpu.util")
        field_names = [fc.field for fc in cpu_change.field_changes]
        assert "interval" in field_names


# ---------------------------------------------------------------------------
# Trigger expression changes → MODIFY
# ---------------------------------------------------------------------------

class TestTriggerExpressionChange:
    def test_changed_expression_is_modify(self):
        desired = _make_template(
            triggers=[{
                "name": "High CPU",
                "expression": "avg(/T_Test/system.cpu.util,5m)>95",
                "severity": "average",
            }],
        )
        current = _make_current(
            triggers=[_zabbix_trigger("20", "High CPU", "avg(/T_Test/system.cpu.util,5m)>90", priority=3)],
        )
        diff = engine.compute_diff(desired, current)
        trigger_change = next(r for r in diff.resource_changes if r.resource_type == "trigger")
        assert trigger_change.type == ChangeType.MODIFY

    def test_modify_has_field_change_for_expression(self):
        desired = _make_template(
            triggers=[{
                "name": "High CPU",
                "expression": "avg(/T_Test/system.cpu.util,5m)>95",
                "severity": "average",
            }],
        )
        current = _make_current(
            triggers=[_zabbix_trigger("20", "High CPU", "avg(/T_Test/system.cpu.util,5m)>90", priority=3)],
        )
        diff = engine.compute_diff(desired, current)
        trigger_change = next(r for r in diff.resource_changes if r.resource_type == "trigger")
        field_names = [fc.field for fc in trigger_change.field_changes]
        assert "expression" in field_names


# ---------------------------------------------------------------------------
# New item in YAML but not in Zabbix → ADD
# ---------------------------------------------------------------------------

class TestNewItemInYaml:
    def test_new_item_is_add(self):
        desired = _make_template(
            items=[
                {"name": "CPU",     "key": "system.cpu.util"},
                {"name": "New item","key": "brand.new.key"},
            ],
        )
        current = _make_current(
            items=[_zabbix_item("10", "system.cpu.util", "CPU")],
        )
        diff = engine.compute_diff(desired, current)
        by_key = {r.key: r for r in diff.resource_changes if r.resource_type == "item"}
        assert by_key["brand.new.key"].type == ChangeType.ADD
        assert by_key["system.cpu.util"].type == ChangeType.UNCHANGED

    def test_new_item_resource_type(self):
        desired = _make_template(items=[{"name": "New", "key": "brand.new.key"}])
        current = _make_current(items=[])
        diff = engine.compute_diff(desired, current)
        new_item = next(r for r in diff.resource_changes if r.key == "brand.new.key")
        assert new_item.resource_type == "item"


# ---------------------------------------------------------------------------
# Item in Zabbix but not in YAML → REMOVE (surfaced as extra)
# ---------------------------------------------------------------------------

class TestExtraItemInZabbix:
    def test_extra_item_is_remove(self):
        """Items present in Zabbix but absent from YAML are flagged as REMOVE."""
        desired = _make_template(items=[])
        current = _make_current(
            items=[_zabbix_item("99", "old.item.key", "Old item")],
        )
        diff = engine.compute_diff(desired, current)
        extra = [r for r in diff.resource_changes if r.key == "old.item.key"]
        assert len(extra) == 1
        assert extra[0].type == ChangeType.REMOVE

    def test_extra_item_has_resource_id(self):
        desired = _make_template(items=[])
        current = _make_current(
            items=[_zabbix_item("99", "old.item.key", "Old item")],
        )
        diff = engine.compute_diff(desired, current)
        extra = next(r for r in diff.resource_changes if r.key == "old.item.key")
        assert extra.resource_id == "99"

    def test_desired_items_unchanged_when_extra_exists(self):
        """An extra Zabbix item does not affect classification of desired items."""
        desired = _make_template(
            items=[{"name": "CPU", "key": "system.cpu.util", "interval": "60s"}],
        )
        current = _make_current(
            items=[
                _zabbix_item("10", "system.cpu.util", "CPU", delay="60s"),
                _zabbix_item("99", "old.item.key", "Old item"),
            ],
        )
        diff = engine.compute_diff(desired, current)
        by_key = {r.key: r for r in diff.resource_changes if r.resource_type == "item"}
        assert by_key["system.cpu.util"].type == ChangeType.UNCHANGED
        assert by_key["old.item.key"].type == ChangeType.REMOVE
