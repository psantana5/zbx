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
    dependent = "dependent"

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
            "dependent": 18,
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
            18: cls.dependent,
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


class PreprocessingType(str, Enum):
    jsonpath = "jsonpath"
    regex = "regex"
    multiplier = "multiplier"
    trim = "trim"
    not_match_regex = "not_match_regex"
    check_not_supported = "check_not_supported"
    discard_unchanged = "discard_unchanged"

    @property
    def zabbix_id(self) -> int:
        return {
            "jsonpath": 12,
            "regex": 5,
            "multiplier": 1,
            "trim": 17,
            "not_match_regex": 8,
            "check_not_supported": 11,
            "discard_unchanged": 19,
        }[self.value]


class Preprocessing(BaseModel):
    type: PreprocessingType
    params: str = ""
    error_handler: int = 0
    error_handler_params: str = ""


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
    # For dependent items: key of the master item prototype in the same rule
    master_item_key: Optional[str] = None
    preprocessing: list[Preprocessing] = Field(default_factory=list)


class TriggerPrototype(BaseModel):
    """Trigger prototype inside a Low-Level Discovery rule."""

    name: str
    expression: str
    severity: TriggerSeverity = TriggerSeverity.average
    recovery_expression: str = ""
    description: str = ""
    allow_manual_close: bool = False
    enabled: bool = True
    tags: list[Tag] = Field(default_factory=list)


class DiscoveryRule(BaseModel):
    name: str
    key: str
    interval: str = "1h"
    type: ItemType = ItemType.zabbix_agent
    description: str = ""
    item_prototypes: list[ItemPrototype] = Field(default_factory=list)
    trigger_prototypes: list[TriggerPrototype] = Field(default_factory=list)


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
# Host configuration (template linking + macros) — the "playbook"
# ---------------------------------------------------------------------------


class HostMacro(BaseModel):
    macro: str   # e.g. {$S3_USER_PASSWORD}
    value: str
    description: str = ""


class Host(BaseModel):
    """Declarative host configuration: which templates to link and which macros to set."""

    host: str                                           # technical host name in Zabbix
    templates: list[str] = Field(default_factory=list)  # template names to link
    macros: list[HostMacro] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Inventory — defines hosts that exist (or should exist) in Zabbix
# Analogous to Ansible inventory
# ---------------------------------------------------------------------------


class HostStatus(str, Enum):
    enabled = "enabled"
    disabled = "disabled"

    @property
    def zabbix_id(self) -> int:
        return 0 if self.value == "enabled" else 1

    @classmethod
    def from_zabbix_id(cls, value: int) -> HostStatus:
        return cls.enabled if value == 0 else cls.disabled


class InventoryHost(BaseModel):
    """A single host entry in the inventory."""

    host: str                          # technical hostname (must be unique in Zabbix)
    name: Optional[str] = None         # visible display name (defaults to host)
    ip: str = "127.0.0.1"
    port: int = 10050
    groups: list[str] = Field(default_factory=lambda: ["Linux servers"])
    description: str = ""
    status: HostStatus = HostStatus.enabled
    # Optionally pre-link templates right from the inventory entry
    templates: list[str] = Field(default_factory=list)
    macros: list[HostMacro] = Field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.name or self.host


class Inventory(BaseModel):
    """Top-level inventory document."""

    hosts: list[InventoryHost] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Connection settings
# ---------------------------------------------------------------------------


class ZabbixSettings(BaseModel):
    url: str
    username: str
    password: str
    verify_ssl: bool = True
    timeout: int = 30
