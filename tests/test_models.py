"""Unit tests for Pydantic models — no Zabbix connection required."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zbx.models import (
    AgentConfig,
    DiscoveryRule,
    HostMacro,
    HostStatus,
    Inventory,
    InventoryHost,
    Item,
    ItemPrototype,
    ItemType,
    ItemValueType,
    Template,
    Trigger,
    TriggerSeverity,
)


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

class TestTemplate:
    def test_valid_template_with_items_triggers_discovery(self):
        t = Template(
            template="T_MyTemplate",
            items=[
                Item(name="CPU", key="system.cpu.util", interval="60s"),
            ],
            triggers=[
                Trigger(name="High CPU", expression="avg(/T_MyTemplate/system.cpu.util,5m)>90"),
            ],
            discovery_rules=[
                DiscoveryRule(
                    name="Net discovery",
                    key="net.if.discovery",
                    item_prototypes=[
                        ItemPrototype(name="{#IF} in", key="net.if.in[{#IF}]"),
                    ],
                    trigger_prototypes=[],
                ),
            ],
        )
        assert t.template == "T_MyTemplate"
        assert len(t.items) == 1
        assert len(t.triggers) == 1
        assert len(t.discovery_rules) == 1

    def test_template_field_required(self):
        with pytest.raises(ValidationError):
            Template()  # type: ignore[call-arg]

    def test_template_empty_string_raises(self):
        with pytest.raises(ValidationError):
            Template(template="")

    def test_template_whitespace_only_raises(self):
        with pytest.raises(ValidationError):
            Template(template="   ")

    def test_display_name_defaults_to_template(self):
        t = Template(template="T_MyTemplate")
        assert t.display_name == "T_MyTemplate"

    def test_display_name_uses_name_when_set(self):
        t = Template(template="T_MyTemplate", name="My Template")
        assert t.display_name == "My Template"

    def test_duplicate_item_keys_raises(self):
        with pytest.raises(ValidationError, match="Duplicate item key"):
            Template(
                template="T_Dup",
                items=[
                    Item(name="Item A", key="same.key"),
                    Item(name="Item B", key="same.key"),
                ],
            )


# ---------------------------------------------------------------------------
# Interval validation
# ---------------------------------------------------------------------------

class TestIntervalValidation:
    @pytest.mark.parametrize("bad", ["abc", "30x", "5mm", "1hh", "s30", "-1s"])
    def test_invalid_interval_raises(self, bad: str):
        with pytest.raises(ValidationError):
            Item(name="X", key="x.key", interval=bad)

    @pytest.mark.parametrize("good", ["30s", "5m", "1h", "0", "{$MACRO}", "2d", "1w", "60"])
    def test_valid_interval_passes(self, good: str):
        item = Item(name="X", key="x.key", interval=good)
        assert item.interval == good

    def test_integer_interval_coerced(self):
        item = Item(name="X", key="x.key", interval=60)  # type: ignore[arg-type]
        assert item.interval == "60"

    def test_macro_interval_passes(self):
        item = Item(name="X", key="x.key", interval="{$UPDATE_INTERVAL}")
        assert item.interval == "{$UPDATE_INTERVAL}"


# ---------------------------------------------------------------------------
# ItemType enum
# ---------------------------------------------------------------------------

class TestItemTypeEnum:
    def test_valid_item_types(self):
        for val in ("zabbix_agent", "zabbix_trapper", "dependent", "http_agent", "calculated"):
            item = Item(name="X", key="x", type=val)  # type: ignore[arg-type]
            assert item.type == ItemType(val)

    def test_invalid_item_type_raises(self):
        with pytest.raises(ValidationError):
            Item(name="X", key="x", type="nonexistent_type")  # type: ignore[arg-type]

    def test_zabbix_id_mapping(self):
        assert ItemType.zabbix_agent.zabbix_id == 0
        assert ItemType.dependent.zabbix_id == 18
        assert ItemType.http_agent.zabbix_id == 19

    def test_from_zabbix_id_roundtrip(self):
        assert ItemType.from_zabbix_id(0) == ItemType.zabbix_agent
        assert ItemType.from_zabbix_id(18) == ItemType.dependent


# ---------------------------------------------------------------------------
# ItemValueType enum
# ---------------------------------------------------------------------------

class TestItemValueTypeEnum:
    def test_valid_value_types(self):
        for val in ("float", "char", "log", "unsigned", "text"):
            item = Item(name="X", key="x", value_type=val)  # type: ignore[arg-type]
            assert item.value_type == ItemValueType(val)

    def test_invalid_value_type_raises(self):
        with pytest.raises(ValidationError):
            Item(name="X", key="x", value_type="integer")  # type: ignore[arg-type]

    def test_zabbix_id_mapping(self):
        assert ItemValueType.float.zabbix_id == 0
        assert ItemValueType.unsigned.zabbix_id == 3
        assert ItemValueType.text.zabbix_id == 4


# ---------------------------------------------------------------------------
# TriggerSeverity
# ---------------------------------------------------------------------------

class TestTriggerSeverity:
    def test_valid_severities(self):
        for val in ("not_classified", "information", "warning", "average", "high", "disaster"):
            t = Trigger(name="T", expression="expr", severity=val)  # type: ignore[arg-type]
            assert t.severity == TriggerSeverity(val)

    def test_invalid_severity_raises(self):
        with pytest.raises(ValidationError):
            Trigger(name="T", expression="expr", severity="critical")  # type: ignore[arg-type]

    def test_severity_zabbix_ids(self):
        assert TriggerSeverity.not_classified.zabbix_id == 0
        assert TriggerSeverity.disaster.zabbix_id == 5

    def test_from_zabbix_id_roundtrip(self):
        assert TriggerSeverity.from_zabbix_id(2) == TriggerSeverity.warning
        assert TriggerSeverity.from_zabbix_id(5) == TriggerSeverity.disaster


# ---------------------------------------------------------------------------
# DiscoveryRule
# ---------------------------------------------------------------------------

class TestDiscoveryRule:
    def test_discovery_rule_with_prototypes_parses(self):
        rule = DiscoveryRule(
            name="Net discovery",
            key="net.if.discovery",
            interval="1h",
            item_prototypes=[
                ItemPrototype(name="{#IF}: In", key="net.if.in[{#IF}]"),
                ItemPrototype(name="{#IF}: Out", key="net.if.out[{#IF}]"),
            ],
            trigger_prototypes=[],
        )
        assert len(rule.item_prototypes) == 2
        assert rule.interval == "1h"

    def test_discovery_rule_defaults(self):
        rule = DiscoveryRule(name="D", key="d.key")
        assert rule.interval == "1h"
        assert rule.item_prototypes == []
        assert rule.trigger_prototypes == []

    def test_discovery_rule_invalid_interval(self):
        with pytest.raises(ValidationError):
            DiscoveryRule(name="D", key="d.key", interval="bad")


# ---------------------------------------------------------------------------
# ItemPrototype with dependent type
# ---------------------------------------------------------------------------

class TestItemPrototype:
    def test_dependent_item_prototype_with_master_key_parses(self):
        proto = ItemPrototype(
            name="{#IF}: Out",
            key="net.if.out[{#IF}]",
            interval="0",
            type=ItemType.dependent,
            master_item_key="net.if.in[{#IF}]",
        )
        assert proto.type == ItemType.dependent
        assert proto.master_item_key == "net.if.in[{#IF}]"

    def test_dependent_item_prototype_without_master_key_parses(self):
        # The model itself does not enforce master_item_key presence
        proto = ItemPrototype(
            name="{#IF}: Out",
            key="net.if.out[{#IF}]",
            type=ItemType.dependent,
        )
        assert proto.master_item_key is None


# ---------------------------------------------------------------------------
# HostMacro
# ---------------------------------------------------------------------------

class TestHostMacro:
    def test_valid_user_macro(self):
        m = HostMacro(macro="{$MY_MACRO}", value="secret")
        assert m.macro == "{$MY_MACRO}"

    def test_valid_lld_macro(self):
        m = HostMacro(macro="{#IFNAME}", value="eth0")
        assert m.macro == "{#IFNAME}"

    def test_invalid_macro_format_raises(self):
        with pytest.raises(ValidationError):
            HostMacro(macro="MY_MACRO", value="secret")

    def test_invalid_macro_no_braces_raises(self):
        with pytest.raises(ValidationError):
            HostMacro(macro="$MY_MACRO", value="val")


# ---------------------------------------------------------------------------
# InventoryHost defaults
# ---------------------------------------------------------------------------

class TestInventoryHost:
    def test_defaults(self):
        h = InventoryHost(host="myhost")
        assert h.ip == "127.0.0.1"
        assert h.port == 10050
        assert h.status == HostStatus.enabled

    def test_status_disabled(self):
        h = InventoryHost(host="myhost", status="disabled")  # type: ignore[arg-type]
        assert h.status == HostStatus.disabled

    def test_display_name_defaults_to_host(self):
        h = InventoryHost(host="myhost")
        assert h.display_name == "myhost"

    def test_display_name_uses_name_when_set(self):
        h = InventoryHost(host="myhost", name="My Host")
        assert h.display_name == "My Host"

    def test_groups_default(self):
        h = InventoryHost(host="myhost")
        assert h.groups == ["Linux servers"]


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

class TestInventory:
    def test_inventory_multiple_hosts(self):
        inv = Inventory(
            hosts=[
                InventoryHost(host="host1", ip="10.0.0.1"),
                InventoryHost(host="host2", ip="10.0.0.2"),
                InventoryHost(host="host3", ip="10.0.0.3"),
            ]
        )
        assert len(inv.hosts) == 3
        assert inv.hosts[1].ip == "10.0.0.2"

    def test_inventory_empty_default(self):
        inv = Inventory()
        assert inv.hosts == []


# ---------------------------------------------------------------------------
# AgentConfig defaults
# ---------------------------------------------------------------------------

class TestAgentConfig:
    def test_sensible_defaults(self):
        a = AgentConfig()
        assert a.ssh_user == "root"
        assert a.ssh_port == 22
        assert a.ssh_key is None
        assert a.sudo is True
        assert a.restart_agent is False
        assert a.scripts == []
        assert a.userparameters == []
        assert a.test_keys == []
