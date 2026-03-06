"""Thin wrapper around the Zabbix JSON-RPC API."""

from __future__ import annotations

import logging
from typing import Any

import requests

from zbx.models import ZabbixSettings

logger = logging.getLogger(__name__)


class ZabbixAPIError(Exception):
    """Raised when the Zabbix API returns an error object."""

    def __init__(self, message: str, code: int = 0) -> None:
        super().__init__(message)
        self.code = code


class ZabbixClient:
    """
    Stateful client for the Zabbix JSON-RPC API.

    Usage::

        with ZabbixClient(settings) as client:
            template = client.get_template("linux-observability")
    """

    def __init__(self, settings: ZabbixSettings) -> None:
        self._settings = settings
        self._session = requests.Session()
        self._session.verify = settings.verify_ssl
        self._session.headers.update({"Content-Type": "application/json-rpc"})
        self._auth: str | None = None
        self._api_url = f"{settings.url.rstrip('/')}/api_jsonrpc.php"
        self._req_id = 0
        # Populated during login(); used to switch between legacy "auth" payload
        # field (< 6.4) and "Authorization: Bearer" header (>= 6.4).
        self._version: tuple[int, ...] = (0, 0, 0)

    # ------------------------------------------------------------------
    # Low-level RPC
    # ------------------------------------------------------------------

    def _call(self, method: str, params: dict[str, Any] | list[Any] | None = None) -> Any:
        self._req_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params if params is not None else {},
            "id": self._req_id,
        }
        # Zabbix < 6.4: auth token goes in the request body.
        # Zabbix >= 6.4: auth token goes in the Authorization header (set in login()).
        if self._auth and self._version < (6, 4):
            payload["auth"] = self._auth

        try:
            resp = self._session.post(
                self._api_url,
                json=payload,
                timeout=self._settings.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ZabbixAPIError(f"HTTP error calling '{method}': {exc}") from exc

        body = resp.json()
        if "error" in body:
            err = body["error"]
            raise ZabbixAPIError(
                f"[{err.get('code')}] {err.get('message')} — {err.get('data')}",
                code=err.get("code", 0),
            )
        return body.get("result")

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def login(self) -> None:
        """Authenticate and store the auth token.

        Detects the Zabbix API version first so that:
        - < 5.4 : uses legacy ``user`` field in user.login
        - >= 5.4 : uses ``username`` field in user.login
        - >= 6.4 : passes token via ``Authorization: Bearer`` header
                   instead of the ``auth`` payload field
        """
        version_str = self.get_api_version()
        self._version = tuple(int(x) for x in version_str.split(".")[:3])
        logger.debug("Zabbix API version: %s", version_str)

        login_params: dict[str, str] = {"password": self._settings.password}
        # "username" key introduced in 5.4; older servers use "user"
        if self._version >= (5, 4):
            login_params["username"] = self._settings.username
        else:
            login_params["user"] = self._settings.username

        result = self._call("user.login", login_params)
        self._auth = result

        # 6.4+ dropped "auth" from the payload — use Bearer header instead
        if self._version >= (6, 4):
            self._session.headers.update({"Authorization": f"Bearer {self._auth}"})

        logger.debug("Authenticated with Zabbix %s at %s", version_str, self._settings.url)

    def logout(self) -> None:
        if self._auth:
            try:
                self._call("user.logout", [])
            except ZabbixAPIError:
                pass
            self._auth = None
            self._session.headers.pop("Authorization", None)

    def get_api_version(self) -> str:
        return self._call("apiinfo.version")  # type: ignore[return-value]

    @property
    def version_str(self) -> str:
        """Return the API version string after login (e.g. '7.4.7')."""
        return ".".join(str(x) for x in self._version) if self._version else "unknown"

    def __enter__(self) -> ZabbixClient:
        self.login()
        return self

    def __exit__(self, *_: object) -> None:
        self.logout()

    # ------------------------------------------------------------------
    # Host groups
    # ------------------------------------------------------------------

    def get_hostgroup(self, name: str) -> dict[str, Any] | None:
        results = self._call("hostgroup.get", {
            "filter": {"name": [name]},
            "output": ["groupid", "name"],
        })
        return results[0] if results else None

    def ensure_hostgroup(self, name: str) -> str:
        """Return the groupid for *name*, creating it if necessary."""
        group = self.get_hostgroup(name)
        if group:
            return str(group["groupid"])
        result = self._call("hostgroup.create", {"name": name})
        gid = str(result["groupids"][0])
        logger.info("Created host group '%s' (id=%s)", name, gid)
        return gid

    # ------------------------------------------------------------------
    # Template groups (Zabbix >= 6.2 split template groups from host groups)
    # ------------------------------------------------------------------

    def get_templategroup(self, name: str) -> dict[str, Any] | None:
        """Look up a template group by name. Returns None on older Zabbix (<6.2)."""
        try:
            results = self._call("templategroup.get", {
                "filter": {"name": [name]},
                "output": ["groupid", "name"],
            })
            return results[0] if results else None
        except ZabbixAPIError:
            # API doesn't exist on Zabbix < 6.2 — caller should fall back to hostgroup
            return None

    def ensure_templategroup(self, name: str) -> str:
        """Return the groupid for template group *name*, creating it if needed.

        On Zabbix >= 6.2 uses templategroup.*; falls back to hostgroup.* on
        older versions so the client works across all supported Zabbix releases.
        """
        if self._version >= (6, 2):
            group = self.get_templategroup(name)
            if group:
                return str(group["groupid"])
            result = self._call("templategroup.create", {"name": name})
            gid = str(result["groupids"][0])
            logger.info("Created template group '%s' (id=%s)", name, gid)
            return gid
        # Fallback for Zabbix < 6.2
        return self.ensure_hostgroup(name)

    # ------------------------------------------------------------------
    # Inventory — host listing and creation
    # ------------------------------------------------------------------

    def list_hosts(self) -> list[dict[str, Any]]:
        """Return all hosts with their groups, interfaces and linked templates."""
        # Zabbix 6.2+ renamed selectGroups → selectHostGroups (response: hostgroups).
        # Normalise to "groups" so callers stay version-agnostic.
        hosts: list[dict[str, Any]] = self._call("host.get", {
            "output": ["hostid", "host", "name", "description", "status"],
            "selectHostGroups": ["groupid", "name"],
            "selectParentTemplates": ["templateid", "host", "name"],
            "selectInterfaces": ["interfaceid", "ip", "port", "type", "main"],
            "selectMacros": ["hostmacroid", "macro", "value", "description"],
        })
        for h in hosts:
            h.setdefault("groups", h.pop("hostgroups", []))
        return hosts

    def create_host(
        self,
        host: str,
        name: str,
        ip: str,
        port: int,
        group_ids: list[str],
        description: str = "",
        status: int = 0,
        template_ids: list[str] | None = None,
    ) -> str:
        params: dict[str, Any] = {
            "host": host,
            "name": name,
            "description": description,
            "status": status,
            "groups": [{"groupid": gid} for gid in group_ids],
            "interfaces": [{
                "type": 1,       # Zabbix agent
                "main": 1,       # default interface
                "useip": 1,
                "ip": ip,
                "dns": "",
                "port": str(port),
            }],
        }
        if template_ids:
            params["templates"] = [{"templateid": tid} for tid in template_ids]
        result = self._call("host.create", params)
        return str(result["hostids"][0])

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    def get_template(self, name: str) -> dict[str, Any] | None:
        results = self._call("template.get", {
            "filter": {"host": [name]},
            "output": ["templateid", "host", "name", "description"],
            "selectGroups": ["groupid", "name"],
        })
        return results[0] if results else None

    def get_template_full(self, name: str) -> dict[str, Any] | None:
        """Fetch a template with all its items, triggers and discovery rules."""
        results = self._call("template.get", {
            "filter": {"host": [name]},
            "output": "extend",
            "selectGroups": ["groupid", "name"],
            "selectItems": [
                "itemid", "name", "key_", "delay", "type",
                "value_type", "units", "description", "status", "history", "trends",
            ],
            "selectMacros": ["hostmacroid", "macro", "value", "description"],
            # NOTE: selectDiscoveryRules intentionally omitted — returns empty on 6.4/7.0.
            # Discovery rules are fetched via discoveryrule.get below.
        })
        if not results:
            return None
        tmpl = results[0]
        # Enrich items with tags (selectItems array can't include sub-entities)
        if tmpl.get("items"):
            item_ids = [i["itemid"] for i in tmpl["items"]]
            items_with_tags = self._call("item.get", {
                "itemids": item_ids,
                "output": ["itemid"],
                "selectTags": "extend",
            })
            tags_by_id = {i["itemid"]: i.get("tags", []) for i in items_with_tags}
            for item in tmpl["items"]:
                item["tags"] = tags_by_id.get(item["itemid"], [])
        # Fetch triggers with expandExpression so expressions are human-readable
        tmpl["triggers"] = self._call("trigger.get", {
            "templateids": [tmpl["templateid"]],
            "output": ["triggerid", "description", "expression", "recovery_expression",
                       "recovery_mode", "priority", "status", "comments"],
            "selectTags": "extend",
            "expandExpression": True,
            "inherited": False,
        })
        # Always fetch discovery rules via discoveryrule.get (reliable on all versions)
        raw_rules: list[dict[str, Any]] = self._call("discoveryrule.get", {  # type: ignore[assignment]
            "templateids": [tmpl["templateid"]],
            "output": ["itemid", "name", "key_", "delay", "type", "master_itemid"],
            "selectFilter": "extend",
            "selectItems": ["itemid", "name", "key_"],
            "selectTriggers": ["triggerid", "description"],
        })
        # Rename sub-entity keys to match what the diff engine expects
        for rule in raw_rules:
            rule["itemPrototypes"] = rule.pop("items", [])
            rule["triggerPrototypes"] = rule.pop("triggers", [])
        tmpl["discoveryRules"] = raw_rules
        return tmpl

    def create_template(
        self,
        host: str,
        name: str,
        description: str,
        group_ids: list[str],
    ) -> str:
        result = self._call("template.create", {
            "host": host,
            "name": name,
            "description": description,
            "groups": [{"groupid": gid} for gid in group_ids],
        })
        return str(result["templateids"][0])

    def update_template(self, templateid: str, **kwargs: Any) -> None:
        self._call("template.update", {"templateid": templateid, **kwargs})

    # ------------------------------------------------------------------
    # Template user macros
    # ------------------------------------------------------------------

    def create_template_macro(
        self, templateid: str, macro: str, value: str, description: str = ""
    ) -> str:
        result = self._call("usermacro.create", {
            "hostid": templateid,
            "macro": macro,
            "value": value,
            "description": description,
        })
        return str(result["hostmacroids"][0])

    def update_template_macro(
        self, hostmacroid: str, value: str, description: str = ""
    ) -> None:
        self._call("usermacro.update", {
            "hostmacroid": hostmacroid,
            "value": value,
            "description": description,
        })

    def delete_template_macro(self, hostmacroid: str) -> None:
        self._call("usermacro.delete", [hostmacroid])

    # ------------------------------------------------------------------
    # Hosts (linking templates + macros)
    # ------------------------------------------------------------------

    def get_host(self, name: str) -> dict[str, Any] | None:
        results = self._call("host.get", {
            "filter": {"host": [name]},
            "output": ["hostid", "host", "name", "status"],
            "selectParentTemplates": ["templateid", "host"],
            "selectMacros": ["hostmacroid", "macro", "value", "description"],
        })
        return results[0] if results else None

    def link_templates(self, hostid: str, template_ids: list[str]) -> None:
        """Add templates to a host without removing existing ones."""
        self._call("host.update", {
            "hostid": hostid,
            "templates": [{"templateid": tid} for tid in template_ids],
        })

    def update_host(self, hostid: str, **kwargs: Any) -> None:
        """Update arbitrary host fields."""
        self._call("host.update", {"hostid": hostid, **kwargs})

    def delete_host(self, hostid: str) -> None:
        """Permanently delete a host from Zabbix."""
        self._call("host.delete", [hostid])

    def get_host_macros(self, hostid: str) -> list[dict[str, Any]]:
        return self._call("usermacro.get", {  # type: ignore[return-value]
            "hostids": [hostid],
            "output": ["hostmacroid", "macro", "value", "description"],
        })

    def create_host_macro(
        self, hostid: str, macro: str, value: str, description: str = ""
    ) -> str:
        result = self._call("usermacro.create", {
            "hostid": hostid,
            "macro": macro,
            "value": value,
            "description": description,
        })
        return str(result["hostmacroids"][0])

    def update_host_macro(
        self, hostmacroid: str, value: str, description: str = ""
    ) -> None:
        self._call("usermacro.update", {
            "hostmacroid": hostmacroid,
            "value": value,
            "description": description,
        })

    # ------------------------------------------------------------------
    # Global macros (usermacro.get with globalmacro=True)
    # ------------------------------------------------------------------

    def list_global_macros(self) -> list[dict[str, Any]]:
        """Return all global macros."""
        return self._call("usermacro.get", {  # type: ignore[return-value]
            "globalmacro": True,
            "output": ["globalmacroid", "macro", "value", "description"],
        })

    def get_global_macro(self, macro: str) -> dict[str, Any] | None:
        results = self._call("usermacro.get", {
            "globalmacro": True,
            "filter": {"macro": [macro]},
            "output": ["globalmacroid", "macro", "value", "description"],
        })
        return results[0] if results else None

    def create_global_macro(self, macro: str, value: str, description: str = "") -> str:
        result = self._call("usermacro.createglobal", {
            "macro": macro,
            "value": value,
            "description": description,
        })
        return str(result["globalmacroids"][0])

    def update_global_macro(
        self, globalmacroid: str, value: str, description: str = ""
    ) -> None:
        self._call("usermacro.updateglobal", {
            "globalmacroid": globalmacroid,
            "value": value,
            "description": description,
        })

    def delete_global_macro(self, globalmacroid: str) -> None:
        self._call("usermacro.deleteglobal", [globalmacroid])

    # ------------------------------------------------------------------
    # Host groups — list all
    # ------------------------------------------------------------------

    def list_hostgroups(self) -> list[dict[str, Any]]:
        """Return all host groups."""
        return self._call("hostgroup.get", {  # type: ignore[return-value]
            "output": ["groupid", "name"],
        })

    def delete_hostgroup(self, groupid: str) -> None:
        self._call("hostgroup.delete", [groupid])

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

    def get_items(self, templateid: str) -> list[dict[str, Any]]:
        return self._call("item.get", {  # type: ignore[return-value]
            "templateids": [templateid],
            "output": [
                "itemid", "name", "key_", "delay", "type",
                "value_type", "units", "description", "status", "history", "trends",
            ],
        })

    def create_item(self, templateid: str, data: dict[str, Any]) -> str:
        result = self._call("item.create", {"hostid": templateid, **data})
        return str(result["itemids"][0])

    def update_item(self, itemid: str, **kwargs: Any) -> None:
        self._call("item.update", {"itemid": itemid, **kwargs})

    # ------------------------------------------------------------------
    # Triggers
    # ------------------------------------------------------------------

    def get_triggers(self, templateid: str) -> list[dict[str, Any]]:
        return self._call("trigger.get", {  # type: ignore[return-value]
            "templateids": [templateid],
            "output": [
                "triggerid", "description", "expression",
                "priority", "status", "comments",
            ],
        })

    def create_trigger(self, data: dict[str, Any]) -> str:
        result = self._call("trigger.create", data)
        return str(result["triggerids"][0])

    def update_trigger(self, triggerid: str, **kwargs: Any) -> None:
        self._call("trigger.update", {"triggerid": triggerid, **kwargs})

    # ------------------------------------------------------------------
    # Discovery rules (LLD)
    # ------------------------------------------------------------------

    def get_discovery_rules(self, templateid: str) -> list[dict[str, Any]]:
        return self._call("discoveryrule.get", {  # type: ignore[return-value]
            "templateids": [templateid],
            "output": ["itemid", "name", "key_", "delay", "type", "description"],
        })

    def create_discovery_rule(self, templateid: str, data: dict[str, Any]) -> str:
        result = self._call("discoveryrule.create", {"hostid": templateid, **data})
        return str(result["itemids"][0])

    def update_discovery_rule(self, itemid: str, **kwargs: Any) -> None:
        self._call("discoveryrule.update", {"itemid": itemid, **kwargs})

    def get_item_prototypes(self, ruleid: str) -> list[dict[str, Any]]:
        return self._call("itemprototype.get", {  # type: ignore[return-value]
            "discoveryids": [ruleid],
            "output": [
                "itemid", "name", "key_", "delay",
                "type", "value_type", "units", "description",
                "params", "master_itemid",
            ],
        })

    def create_item_prototype(
        self, ruleid: str, templateid: str, data: dict[str, Any]
    ) -> str:
        result = self._call("itemprototype.create", {
            "ruleid": ruleid,
            "hostid": templateid,
            **data,
        })
        return str(result["itemids"][0])

    def update_item_prototype(self, itemid: str, **kwargs: Any) -> None:
        self._call("itemprototype.update", {"itemid": itemid, **kwargs})

    def get_trigger_prototypes(self, ruleid: str) -> list[dict[str, Any]]:
        return self._call("triggerprototype.get", {  # type: ignore[return-value]
            "discoveryids": [ruleid],
            "output": [
                "triggerid", "description", "expression",
                "priority", "status", "recovery_expression", "manual_close",
            ],
            "expandExpression": True,
        })

    def create_trigger_prototype(self, data: dict[str, Any]) -> str:
        result = self._call("triggerprototype.create", data)
        return str(result["triggerids"][0])

    def update_trigger_prototype(self, triggerid: str, **kwargs: Any) -> None:
        self._call("triggerprototype.update", {"triggerid": triggerid, **kwargs})

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------

    def export_template_raw(self, templateid: str) -> dict[str, Any] | None:
        """Return full template data including items, triggers, discovery rules."""
        results = self._call("template.get", {
            "templateids": [templateid],
            "output": "extend",
            "selectGroups": ["groupid", "name"],
            "selectItems": "extend",
            "selectMacros": ["hostmacroid", "macro", "value", "description"],
            # NOTE: selectDiscoveryRules is intentionally omitted here —
            # on Zabbix 6.4/7.0 template.get returns an empty list for discoveryRules
            # even when they exist. We always fetch them via discoveryrule.get below.
        })
        if not results:
            return None
        tmpl = results[0]
        # Enrich items with tags (selectItems:extend doesn't include tags sub-entity)
        if tmpl.get("items"):
            item_ids = [i["itemid"] for i in tmpl["items"]]
            items_with_tags = self._call("item.get", {
                "itemids": item_ids,
                "output": ["itemid"],
                "selectTags": "extend",
            })
            tags_by_id = {i["itemid"]: i.get("tags", []) for i in items_with_tags}
            for item in tmpl["items"]:
                item["tags"] = tags_by_id.get(item["itemid"], [])
        # Fetch triggers separately with expandExpression to get human-readable expressions
        tmpl["triggers"] = self._call("trigger.get", {
            "templateids": [templateid],
            "output": ["triggerid", "description", "expression", "priority",
                       "status", "comments", "recovery_expression", "recovery_mode"],
            "selectTags": "extend",
            "expandExpression": True,
            "inherited": False,
        })
        # Fetch ALL discovery rules via discoveryrule.get — this is authoritative on
        # all Zabbix versions (6.4, 7.0, 7.4+) unlike template.get+selectDiscoveryRules.
        raw_rules: list[dict[str, Any]] = self._call("discoveryrule.get", {  # type: ignore[assignment]
            "templateids": [templateid],
            "output": "extend",
            "selectFilter": "extend",
        })
        tmpl["discoveryRules"] = raw_rules
        for rule in tmpl["discoveryRules"]:
            rule["itemPrototypes"] = self.get_item_prototypes(rule["itemid"])
            rule["triggerPrototypes"] = self.get_trigger_prototypes(rule["itemid"])
        return tmpl

    def find_templates(self, search: str) -> list[dict[str, Any]]:
        """Search templates by name. Prefers exact match; falls back to partial."""
        exact = self._call("template.get", {  # type: ignore[return-value]
            "filter": {"host": [search]},
            "output": ["templateid", "host", "name", "description"],
        })
        if exact:
            return exact
        return self._call("template.get", {  # type: ignore[return-value]
            "search": {"host": search, "name": search},
            "searchByAny": True,
            "output": ["templateid", "host", "name", "description"],
        })

    def get_all_templates(self) -> list[dict[str, Any]]:
        """Return every template in Zabbix (id + name only)."""
        return self._call("template.get", {  # type: ignore[return-value]
            "output": ["templateid", "host", "name"],
        })
