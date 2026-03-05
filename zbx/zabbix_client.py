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
        if self._auth:
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
        """Authenticate and store the auth token."""
        # Zabbix 5.4+ uses "username"; older versions use "user"
        try:
            result = self._call("user.login", {
                "username": self._settings.username,
                "password": self._settings.password,
            })
        except ZabbixAPIError:
            result = self._call("user.login", {
                "user": self._settings.username,
                "password": self._settings.password,
            })
        self._auth = result
        logger.debug("Authenticated with Zabbix at %s", self._settings.url)

    def logout(self) -> None:
        if self._auth:
            try:
                self._call("user.logout", [])
            except ZabbixAPIError:
                pass
            self._auth = None

    def get_api_version(self) -> str:
        return self._call("apiinfo.version")  # type: ignore[return-value]

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
            "selectTriggers": [
                "triggerid", "description", "expression", "priority", "status", "comments",
            ],
            "selectDiscoveryRules": ["itemid", "name", "key_", "delay", "type"],
        })
        return results[0] if results else None

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
            "selectTriggers": "extend",
            "selectDiscoveryRules": "extend",
        })
        if not results:
            return None
        tmpl = results[0]
        # Enrich each discovery rule with its item prototypes
        for rule in tmpl.get("discoveryRules", []):
            rule["itemPrototypes"] = self.get_item_prototypes(rule["itemid"])
        return tmpl

    def find_templates(self, search: str) -> list[dict[str, Any]]:
        """Search templates by name (case-insensitive partial match)."""
        return self._call("template.get", {  # type: ignore[return-value]
            "search": {"host": search, "name": search},
            "searchByAny": True,
            "output": ["templateid", "host", "name", "description"],
        })
