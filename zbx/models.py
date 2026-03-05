"""Pydantic models for Zabbix configuration objects."""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


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

    @classmethod
    def _missing_(cls, value: object) -> "TriggerSeverity | None":
        # Accept common aliases
        aliases = {"info": cls.information}
        return aliases.get(str(value).lower())


class ItemType(str, Enum):
    zabbix_agent = "zabbix_agent"
    zabbix_trapper = "zabbix_trapper"
    simple_check = "simple_check"
    snmp_v1 = "snmp_v1"
    zabbix_internal = "zabbix_internal"
    snmp_v2c = "snmp_v2c"
    snmp_v3 = "snmp_v3"
    zabbix_agent_active = "zabbix_agent_active"
    telnet_agent = "telnet_agent"
    ipmi_agent = "ipmi_agent"
    ssh_agent = "ssh_agent"
    db_monitor = "db_monitor"
    calculated = "calculated"
    jmx_agent = "jmx_agent"
    snmp_trap = "snmp_trap"
    dependent = "dependent"
    http_agent = "http_agent"
    external = "external"
    script = "script"

    @property
    def zabbix_id(self) -> int:
        return {
            "zabbix_agent": 0,
            "zabbix_trapper": 2,
            "simple_check": 3,
            "snmp_v1": 1,
            "zabbix_internal": 5,
            "snmp_v2c": 4,
            "snmp_v3": 6,
            "zabbix_agent_active": 7,
            "telnet_agent": 14,
            "ipmi_agent": 12,
            "ssh_agent": 13,
            "db_monitor": 11,
            "calculated": 15,
            "jmx_agent": 16,
            "snmp_trap": 17,
            "dependent": 18,
            "http_agent": 19,
            "external": 10,
            "script": 21,
        }[self.value]

    @classmethod
    def from_zabbix_id(cls, value: int) -> ItemType:
        return {
            0: cls.zabbix_agent,
            1: cls.snmp_v1,
            2: cls.zabbix_trapper,
            3: cls.simple_check,
            4: cls.snmp_v2c,
            5: cls.zabbix_internal,
            6: cls.snmp_v3,
            7: cls.zabbix_agent_active,
            10: cls.external,
            11: cls.db_monitor,
            12: cls.ipmi_agent,
            13: cls.ssh_agent,
            14: cls.telnet_agent,
            15: cls.calculated,
            16: cls.jmx_agent,
            17: cls.snmp_trap,
            18: cls.dependent,
            19: cls.http_agent,
            21: cls.script,
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

# Valid Zabbix interval: digits with optional s/m/h/d/w suffix,
# user macros {$VAR}, or "0" (disabled). Also allow flexible intervals like "30s;0-7,00:00-24:00".
_INTERVAL_RE = re.compile(
    r"^(0|\d+[smhdw]?|"          # plain seconds/minutes/etc or 0
    r"\{[$#][A-Za-z0-9_\.]+\}|"  # user/LLD macro
    r"\d+[smhdw]?;.+)$"          # flexible scheduling
)


def _validate_interval(v: object) -> str:
    s = str(v) if isinstance(v, int) else v  # coerce YAML integers (60 → "60")
    if not isinstance(s, str):
        raise ValueError(f"interval must be a string, got {type(v).__name__}")
    if s and not _INTERVAL_RE.match(s):
        raise ValueError(
            f"Invalid interval '{s}'. Use a number with optional suffix (s/m/h/d/w), "
            "e.g. '60s', '5m', '1h', or '0' to disable."
        )
    return s


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
    params: str = ""   # formula for calculated items; OID for SNMP; empty otherwise
    url: Optional[str] = None   # required for http_agent type
    master_item_key: Optional[str] = None  # required for dependent items
    tags: list[Tag] = Field(default_factory=list)
    history: str = "90d"
    trends: str = "365d"
    enabled: bool = True

    @field_validator("interval", mode="before")
    @classmethod
    def validate_interval(cls, v: object) -> str:
        return _validate_interval(v)


class ItemPrototype(BaseModel):
    """Item prototype inside a Low-Level Discovery rule."""

    name: str
    key: str
    interval: str = "60s"
    type: ItemType = ItemType.zabbix_agent
    value_type: ItemValueType = ItemValueType.float
    units: str = ""
    description: str = ""
    params: str = ""  # formula for calculated; empty for other types
    # For dependent items: key of the master item prototype in the same rule
    master_item_key: Optional[str] = None
    preprocessing: list[Preprocessing] = Field(default_factory=list)

    @field_validator("interval", mode="before")
    @classmethod
    def validate_interval(cls, v: object) -> str:
        return _validate_interval(v)


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


class LLDFilterConditionOperator(str, Enum):
    matches_regex = "matches_regex"
    does_not_match_regex = "does_not_match_regex"

    @property
    def zabbix_id(self) -> int:
        return {"matches_regex": 8, "does_not_match_regex": 12}[self.value]

    @classmethod
    def from_zabbix_id(cls, zid: int) -> "LLDFilterConditionOperator":
        _map = {8: cls.matches_regex, 12: cls.does_not_match_regex}
        return _map.get(zid, cls.matches_regex)


class LLDFilterCondition(BaseModel):
    macro: str
    value: str = ""
    operator: LLDFilterConditionOperator = LLDFilterConditionOperator.matches_regex


class LLDFilterEvalType(str, Enum):
    and_or = "and_or"
    and_ = "and"
    or_ = "or"
    formula = "formula"

    @property
    def zabbix_id(self) -> int:
        return {"and_or": 0, "and": 1, "or": 2, "formula": 3}[self.value]

    @classmethod
    def from_zabbix_id(cls, zid: int) -> "LLDFilterEvalType":
        _map = {0: cls.and_or, 1: cls.and_, 2: cls.or_, 3: cls.formula}
        return _map.get(zid, cls.and_or)


class LLDFilter(BaseModel):
    evaltype: LLDFilterEvalType = LLDFilterEvalType.and_or
    conditions: list[LLDFilterCondition] = Field(default_factory=list)
    formula: str = ""  # custom formula when evaltype=formula

    @model_validator(mode="before")
    @classmethod
    def accept_list_shorthand(cls, v: object) -> object:
        # Allow: filter: [{macro: ..., value: ...}, ...] as shorthand for conditions list
        if isinstance(v, list):
            return {"conditions": v}
        return v


class DiscoveryRule(BaseModel):
    name: str
    key: str
    interval: str = "1h"
    type: ItemType = ItemType.zabbix_agent
    description: str = ""
    master_item_key: Optional[str] = None  # required for dependent discovery rules
    filter: Optional[LLDFilter] = None
    item_prototypes: list[ItemPrototype] = Field(default_factory=list)
    trigger_prototypes: list[TriggerPrototype] = Field(default_factory=list)

    @field_validator("interval", mode="before")
    @classmethod
    def validate_interval(cls, v: object) -> str:
        return _validate_interval(v)


class Trigger(BaseModel):
    name: str
    expression: str
    severity: TriggerSeverity = TriggerSeverity.average
    recovery_expression: str = ""
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
    # Optional agent deployment block — used by checks in configs/checks/<name>/check.yaml
    # to bundle script deployment info alongside the Zabbix template definition.
    # Ignored by `zbx apply`; consumed by `zbx agent deploy --from-check <path>`.
    agent: Optional["AgentConfig"] = None

    @field_validator("template")
    @classmethod
    def template_name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("template name must not be empty")
        return v

    @field_validator("items")
    @classmethod
    def no_duplicate_item_keys(cls, items: list) -> list:
        seen: dict[str, int] = {}
        for i, item in enumerate(items):
            key = item.key
            if key in seen:
                raise ValueError(
                    f"Duplicate item key '{key}' at position {i} "
                    f"(first seen at position {seen[key]})"
                )
            seen[key] = i
        return items

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

    @field_validator("macro")
    @classmethod
    def macro_format(cls, v: str) -> str:
        if "{$" not in v and "{#" not in v:
            raise ValueError(
                f"macro must contain '{{$' (user macro) or '{{#' (LLD macro), got '{v}'"
            )
        return v


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


# ---------------------------------------------------------------------------
# Agent configuration — deploy scripts and UserParameters to monitored hosts
# ---------------------------------------------------------------------------


class UserParameter(BaseModel):
    """A single UserParameter line: key → shell command."""

    key: str        # e.g. s3.user.discover  or  s3.user.metrics[*]
    command: str    # e.g. /usr/local/scripts/zabbix/getS3Storage.py $1 $2 $3


class UserParametersFile(BaseModel):
    """One .conf file dropped into zabbix_agentd.d/."""

    name: str                              # logical name, used for the filename if path omitted
    path: Optional[str] = None             # absolute path on remote host; defaults to
                                           # /etc/zabbix/zabbix_agentd.d/<name>.conf
    parameters: list[UserParameter] = Field(default_factory=list)

    @property
    def remote_path(self) -> str:
        return self.path or f"/etc/zabbix/zabbix_agentd.d/{self.name}.conf"


class ScriptDeploy(BaseModel):
    """A script file to copy from the local repo to the remote host."""

    source: str          # path relative to the repo root, e.g. scripts/getS3Storage.py
    dest: str            # absolute path on the remote host
    owner: str = "zabbix"
    group: str = "zabbix"
    mode: str = "0755"   # chmod-style octal string


class AgentConfig(BaseModel):
    """SSH-based agent deployment config attached to an inventory host."""

    ssh_user: str = "root"
    ssh_port: int = 22
    ssh_key: Optional[str] = None          # path to private key; None = use SSH agent / default key
    sudo: bool = True                      # use sudo for chown / writing to /etc/zabbix/
    scripts: list[ScriptDeploy] = Field(default_factory=list)
    userparameters: list[UserParametersFile] = Field(default_factory=list)
    restart_agent: bool = False            # restart zabbix-agentd after deploy
    test_keys: list[str] = Field(default_factory=list)  # keys to test with zabbix_agentd -t


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
    # Agent-side deployment (scripts + userparameters)
    agent: Optional[AgentConfig] = None

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
