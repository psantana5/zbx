"""Serialize and deserialize zbx plan output to/from JSON.

A saved plan captures:
- the config path that was planned
- the timestamp
- the computed template and host diffs

``zbx apply --from-plan plan.json`` reads this file, shows the saved
diffs for review, then applies the original configs from the saved path.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zbx.deployer import HostDiff, MacroChange
from zbx.diff_engine import ChangeType, FieldChange, ResourceChange, TemplateDiff

# ---------------------------------------------------------------------------
# Serialise
# ---------------------------------------------------------------------------

def _field_change_to_dict(fc: FieldChange) -> dict[str, Any]:
    return {"field": fc.field, "old_value": fc.old_value, "new_value": fc.new_value}


def _resource_change_to_dict(rc: ResourceChange) -> dict[str, Any]:
    return {
        "type": rc.type.value,
        "resource_type": rc.resource_type,
        "name": rc.name,
        "key": rc.key,
        "resource_id": rc.resource_id,
        "field_changes": [_field_change_to_dict(f) for f in rc.field_changes],
    }


def _template_diff_to_dict(td: TemplateDiff) -> dict[str, Any]:
    return {
        "template_name": td.template_name,
        "template_change": td.template_change.value,
        "template_id": td.template_id,
        "field_changes": [_field_change_to_dict(f) for f in td.field_changes],
        "resource_changes": [_resource_change_to_dict(r) for r in td.resource_changes],
        "warnings": td.warnings,
    }


def _macro_change_to_dict(mc: MacroChange) -> dict[str, Any]:
    return {
        "macro": mc.macro,
        "type": mc.type.value,
        "old_value": mc.old_value,
        "new_value": mc.new_value,
    }


def _host_diff_to_dict(hd: HostDiff) -> dict[str, Any]:
    return {
        "host_name": hd.host_name,
        "found": hd.found,
        "templates_to_link": hd.templates_to_link,
        "templates_already_linked": hd.templates_already_linked,
        "macro_changes": [_macro_change_to_dict(m) for m in hd.macro_changes],
    }


def save_plan(
    configs_path: Path,
    template_diffs: list[TemplateDiff],
    host_diffs: list[HostDiff],
    output: Path,
) -> None:
    """Write a plan to *output* as JSON."""
    payload = {
        "zbx_plan_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "configs_path": str(configs_path.resolve()),
        "template_diffs": [_template_diff_to_dict(d) for d in template_diffs],
        "host_diffs": [_host_diff_to_dict(d) for d in host_diffs],
    }
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Deserialise
# ---------------------------------------------------------------------------

def _dict_to_field_change(d: dict[str, Any]) -> FieldChange:
    return FieldChange(field=d["field"], old_value=d["old_value"], new_value=d["new_value"])


def _dict_to_resource_change(d: dict[str, Any]) -> ResourceChange:
    return ResourceChange(
        type=ChangeType(d["type"]),
        resource_type=d["resource_type"],
        name=d["name"],
        key=d.get("key"),
        resource_id=d.get("resource_id"),
        field_changes=[_dict_to_field_change(f) for f in d.get("field_changes", [])],
    )


def _dict_to_template_diff(d: dict[str, Any]) -> TemplateDiff:
    return TemplateDiff(
        template_name=d["template_name"],
        template_change=ChangeType(d["template_change"]),
        template_id=d.get("template_id"),
        field_changes=[_dict_to_field_change(f) for f in d.get("field_changes", [])],
        resource_changes=[_dict_to_resource_change(r) for r in d.get("resource_changes", [])],
        warnings=d.get("warnings", []),
    )


def _dict_to_macro_change(d: dict[str, Any]) -> MacroChange:
    return MacroChange(
        macro=d["macro"],
        type=ChangeType(d["type"]),
        old_value=d.get("old_value", ""),
        new_value=d.get("new_value", ""),
    )


def _dict_to_host_diff(d: dict[str, Any]) -> HostDiff:
    return HostDiff(
        host_name=d["host_name"],
        found=d["found"],
        templates_to_link=d.get("templates_to_link", []),
        templates_already_linked=d.get("templates_already_linked", []),
        macro_changes=[_dict_to_macro_change(m) for m in d.get("macro_changes", [])],
    )


class SavedPlan:
    """A deserialized saved plan ready for display or apply."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.version: int = payload.get("zbx_plan_version", 1)
        self.created_at: str = payload.get("created_at", "")
        self.configs_path: Path = Path(payload["configs_path"])
        self.template_diffs: list[TemplateDiff] = [
            _dict_to_template_diff(d) for d in payload.get("template_diffs", [])
        ]
        self.host_diffs: list[HostDiff] = [
            _dict_to_host_diff(d) for d in payload.get("host_diffs", [])
        ]

    @classmethod
    def load(cls, path: Path) -> "SavedPlan":
        """Load a plan from a JSON file."""
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"Cannot read plan file {path}: {exc}") from exc
        if data.get("zbx_plan_version") != 1:
            raise ValueError(f"Unsupported plan version: {data.get('zbx_plan_version')}")
        return cls(data)

    @property
    def has_changes(self) -> bool:
        return any(d.has_changes for d in self.template_diffs) or any(
            d.has_changes for d in self.host_diffs
        )
