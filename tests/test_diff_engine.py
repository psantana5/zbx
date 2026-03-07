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
# Tests for TemplateDiff.has_changes
# ---------------------------------------------------------------------------

def test_template_diff_has_changes_add():
    diff = TemplateDiff(
        template_name="T_Test",
        template_change=ChangeType.ADD,
        template_id=None,
    )
    assert diff.has_changes is True

def test_template_diff_has_changes_remove():
    diff = TemplateDiff(
        template_name="T_Test",
        template_change=ChangeType.REMOVE,
        template_id="1001",
    )
    assert diff.has_changes is True

def test_template_diff_has_changes_modify():
    diff = TemplateDiff(
        template_name="T_Test",
        template_change=ChangeType.MODIFY,
        template_id="1001",
        field_changes=[
            FieldChange(field="name", old_value="Old Name", new_value="New Name")
        ],
    )
    assert diff.has_changes is True

def test_template_diff_has_changes_unchanged():
    diff = TemplateDiff(
        template_name="T_Test",
        template_change=ChangeType.UNCHANGED,
        template_id="1001",
    )
    assert diff.has_changes is False


# ---------------------------------------------------------------------------
# Tests for TemplateDiff.summary
# ---------------------------------------------------------------------------

def test_template_diff_summary():
    diff = TemplateDiff(
        template_name="T_Test",
        template_change=ChangeType.MODIFY,
        template_id="1001",
        field_changes=[
            FieldChange(field="name", old_value="Old Name", new_value="New Name")
        ],
        resource_changes=[
            ResourceChange(
                type=ChangeType.ADD, resource_type="item", name="New Item", key="key1"
            ),
            ResourceChange(
                type=ChangeType.MODIFY, resource_type="trigger", name="Trigger 1"
            ),
            ResourceChange(
                type=ChangeType.REMOVE, resource_type="item", name="Old Item", key="key2"
            ),
            ResourceChange(
                type=ChangeType.UNCHANGED, resource_type="item", name="Unchanged Item"
            ),
        ],
    )
    summary = diff.summary
    assert summary == {
        "add": 1,
        "modify": 2,  # 1 template field change + 1 resource modify
        "remove": 1,
    }