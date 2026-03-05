"""Pydantic models for Zabbix configuration objects."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations with bidirectional Zabbix API ID mapping
# ---------------------------------------------------------------------------


class TriggerSeverity(str, Enum):
    not_classified = "not_classified"
    information = "information"
    warning = "warning"
    average = "average"
    high = "high"
    disaster = "disaster"

    @property
    def zabbix_id(self) -> int:
        return {
            "not_classified": 0,
            "information": 1,
            "warning": 2,
            "average": 3,
            "high": 4,
            "disaster": 5,
        }[self.value]

    @classmethod
    def from_zabbix_id(cls, value: int) -> TriggerSeverity:
        return {
            0: cls.not_classified,
            1: cls.information,
            2: cls.warning,
            3: cls.average,
            4: cls.high,
            5: cls.disaster,
        }.get(value, cls.not_classified)


class ItemType(str, Enum):
    zabbix_agent = "zabbix_agent"
    zabbix_trapper = "zabbix_trapper"
    simple_check = "simple_check"
    zabbix_internal = "zabbix_internal"
    zabbix_agent_active = "zabbix_agent_active"
    calculated = "calculated"
    http_agent = "http_agent"
    snmp_v2c = "snmp_v2c"

    @property
    def zabbix_id(self) -> int:
        return {
            "zabbix_agent": 0,
            "zabbix_trapper": 2,
            "simple_check": 3,
            "zabbix_internal": 5,
            "zabbix_agent_active": 7,
            "calculated": 15,
            "http_agent": 19,
            "snmp_v2c": 4,
        }[self.value]

    @classmethod
    def from_zabbix_id(cls, value: int) -> ItemType:
        return {
            0: cls.zabbix_agent,
            2: cls.zabbix_trapper,
            3: cls.simple_check,
            4: cls.snmp_v2c,
            5: cls.zabbix_internal,
            7: cls.zabbix_agent_active,
            15: cls.calculated,
            19: cls.http_agent,
        }.get(value, cls.zabbix_agent)


class ItemValueType(str, Enum):
    float = "float"
    char = "char"
    log = "log"
    unsigned = "unsigned"
    text = "text"

    @property
    def zabbix_id(self) -> int:
        return {
            "float": 0,
            "char": 1,
            "log": 2,
            "unsigned": 3,
            "text": 4,
        }[self.value]

    @classmethod
    def from_zabbix_id(cls, value: int) -> ItemValueType:
        return {
            0: cls.float,
            1: cls.char,
            2: cls.log,
            3: cls.unsigned,
            4: cls.text,
        }.get(value, cls.float)


# ---------------------------------------------------------------------------
# Configuration models
# ---------------------------------------------------------------------------


class Tag(BaseModel):
    tag: str
    value: str = ""


class Item(BaseModel):
    name: str
    key: str
    interval: str = "60s"
    type: ItemType = ItemType.zabbix_agent
    value_type: ItemValueType = ItemValueType.float
    units: str = ""
    description: str = ""
    tags: list[Tag] = Field(default_factory=list)
    history: str = "90d"
    trends: str = "365d"
    enabled: bool = True


class ItemPrototype(BaseModel):
    """Item prototype inside a Low-Level Discovery rule."""

    name: str
    key: str
    interval: str = "60s"
    type: ItemType = ItemType.zabbix_agent
    value_type: ItemValueType = ItemValueType.float
    units: str = ""
    description: str = ""


class DiscoveryRule(BaseModel):
    name: str
    key: str
    interval: str = "1h"
    type: ItemType = ItemType.zabbix_agent
    description: str = ""
    item_prototypes: list[ItemPrototype] = Field(default_factory=list)


class Trigger(BaseModel):
    name: str
    expression: str
    severity: TriggerSeverity = TriggerSeverity.average
    description: str = ""
    enabled: bool = True
    tags: list[Tag] = Field(default_factory=list)


class Template(BaseModel):
    template: str  # technical host name in Zabbix
    name: Optional[str] = None  # visible display name (defaults to template)
    description: str = ""
    groups: list[str] = Field(default_factory=lambda: ["Templates"])
    items: list[Item] = Field(default_factory=list)
    triggers: list[Trigger] = Field(default_factory=list)
    discovery_rules: list[DiscoveryRule] = Field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.name or self.template


# ---------------------------------------------------------------------------
# Connection settings
# ---------------------------------------------------------------------------


class ZabbixSettings(BaseModel):
    url: str
    username: str
    password: str
    verify_ssl: bool = True
    timeout: int = 30
