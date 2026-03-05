#!/usr/bin/env python3
"""
verify_check_values.py — Live Zabbix metric verifier
=====================================================
Connects to the Zabbix API and confirms that each item in the
'system-health' template is actually delivering data for the target host.

Usage:
    python3 scripts/verify_check_values.py
    python3 scripts/verify_check_values.py --host zabbixtest3100 --wait 90

Requires the same .env as zbx (ZBX_URL, ZBX_USER, ZBX_PASSWORD).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values

# ── ANSI colours ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# ── Zabbix value-type labels ───────────────────────────────────────────────────
VALUE_TYPE_LABEL = {0: "float", 1: "char", 2: "log", 3: "unsigned", 4: "text"}

HISTORY_TABLE = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}   # type → history type

# ── Items we expect to have data for (key prefix → human label) ───────────────
EXPECTED_KEYS = [
    "system.cpu.load[all,avg1]",
    "system.cpu.load[all,avg5]",
    "system.cpu.load[all,avg15]",
    "system.cpu.num",
    "vm.memory.size[total]",
    "vm.memory.size[available]",
    "vm.memory.size[used]",
    "vfs.fs.size[/,free]",
    "vfs.fs.size[/,total]",
    "vfs.fs.size[/,pfree]",
    "system.uptime",
    "system.hostname",
    "proc.num[zabbix_agentd]",
]


# ── Low-level API wrapper ──────────────────────────────────────────────────────

class ZabbixAPI:
    def __init__(self, url: str, user: str, password: str, verify_ssl: bool = True) -> None:
        self._url = url.rstrip("/") + "/api_jsonrpc.php"
        self._session = requests.Session()
        self._session.verify = verify_ssl
        self._token: str | None = None
        self._version: tuple[int, ...] = (0,)
        self._login(user, password)

    def _call(self, method: str, params: dict) -> Any:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._token and self._version >= (5, 4):
            headers["Authorization"] = f"Bearer {self._token}"

        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }
        if self._token and self._version < (5, 4):
            payload["auth"] = self._token

        resp = self._session.post(self._url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Zabbix API error: {data['error']}")
        return data["result"]

    def _login(self, user: str, password: str) -> None:
        # get version first (no auth needed)
        raw = self._call("apiinfo.version", {})
        parts = tuple(int(x) for x in str(raw).split("."))
        self._version = parts

        if self._version >= (5, 4):
            self._token = self._call("user.login", {"username": user, "password": password})
        else:
            self._token = self._call("user.login", {"user": user, "password": password})

    # -- helpers ---------------------------------------------------------------

    def find_host(self, hostname: str) -> dict | None:
        results = self._call("host.get", {
            "filter": {"host": [hostname]},
            "output": ["hostid", "host", "status"],
            "selectParentTemplates": ["host", "templateid"],
        })
        return results[0] if results else None

    def get_items(self, hostid: str) -> list[dict]:
        return self._call("item.get", {
            "hostids": [hostid],
            "output": ["itemid", "name", "key_", "value_type", "lastvalue",
                       "lastclock", "state", "error", "status"],
            "sortfield": "key_",
        })

    def get_history(self, itemid: str, value_type: int, limit: int = 5) -> list[dict]:
        return self._call("history.get", {
            "itemids": [itemid],
            "history": value_type,
            "sortfield": "clock",
            "sortorder": "DESC",
            "limit": limit,
            "output": "extend",
        })


# ── Formatting helpers ─────────────────────────────────────────────────────────

def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _human_uptime(secs: int) -> str:
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def _format_value(key: str, raw: str, vtype: int) -> str:
    try:
        if vtype == 3:   # unsigned int
            n = int(raw)
            if any(x in key for x in ("size", "memory", "free", "used", "total")):
                return _human_bytes(n)
            if "uptime" in key:
                return _human_uptime(n)
            return str(n)
        if vtype == 0:   # float
            f = float(raw)
            if "pfree" in key or "pused" in key:
                return f"{f:.1f}%"
            return f"{f:.4f}"
        return raw  # char / text / log
    except (ValueError, TypeError):
        return raw


def _age(clock: str) -> str:
    try:
        ts = int(clock)
        age_s = int(time.time()) - ts
        if age_s < 60:
            return f"{age_s}s ago"
        if age_s < 3600:
            return f"{age_s // 60}m ago"
        return f"{age_s // 3600}h ago"
    except (ValueError, TypeError):
        return "?"


# ── Main verification logic ────────────────────────────────────────────────────

def verify(hostname: str, template: str, wait_secs: int, env: dict[str, str]) -> int:
    url      = env.get("ZBX_URL", "http://localhost/zabbix")
    user     = env.get("ZBX_USER", "Admin")
    password = env.get("ZBX_PASSWORD", "zabbix")
    verify_ssl = env.get("ZBX_VERIFY_SSL", "true").lower() not in ("false", "0", "no")

    print(f"\n{BOLD}{CYAN}━━━  Zabbix check verifier  ━━━{RESET}")
    print(f"  Server : {url}")
    print(f"  Host   : {hostname}")
    print(f"  Template: {template}\n")

    api = ZabbixAPI(url, user, password, verify_ssl=verify_ssl)
    print(f"{DIM}  Zabbix {'.'.join(str(x) for x in api._version)}{RESET}\n")

    # -- find host ------------------------------------------------------------
    host = api.find_host(hostname)
    if not host:
        print(f"{RED}✗ Host '{hostname}' not found in Zabbix.{RESET}")
        print("  Run:  zbx inventory apply inventory.yaml")
        return 1

    linked = [t["host"] for t in host.get("parentTemplates", [])]
    if template not in linked:
        print(f"{YELLOW}⚠ Template '{template}' is NOT linked to '{hostname}'.{RESET}")
        print(f"  Linked templates: {linked or ['none']}")
        print(f"  Run:  zbx inventory apply inventory.yaml  (add templates: [{template}])")
    else:
        print(f"{GREEN}✔ Template '{template}' is linked to '{hostname}'.{RESET}")

    hostid = host["hostid"]

    # -- wait for data if requested -------------------------------------------
    deadline = time.time() + wait_secs
    if wait_secs > 0:
        print(f"\n{DIM}  Waiting up to {wait_secs}s for first data points…{RESET}")

    while True:
        items_raw = api.get_items(hostid)
        items_by_key = {i["key_"]: i for i in items_raw}

        # Check if all expected keys have data yet
        keys_with_data = sum(
            1 for k in EXPECTED_KEYS
            if k in items_by_key and items_by_key[k].get("lastclock", "0") != "0"
        )

        if keys_with_data >= len(EXPECTED_KEYS) // 2 or time.time() > deadline:
            break

        remaining = int(deadline - time.time())
        sys.stdout.write(f"\r  {keys_with_data}/{len(EXPECTED_KEYS)} items have data  ({remaining}s remaining)   ")
        sys.stdout.flush()
        time.sleep(5)

    if wait_secs > 0:
        print()

    # -- print results table ---------------------------------------------------
    print(f"\n{BOLD}  {'KEY':<42} {'VALUE':<22} {'AGE':<12} {'STATUS'}{RESET}")
    print("  " + "─" * 88)

    total = received = errors = 0

    for key in EXPECTED_KEYS:
        total += 1
        item = items_by_key.get(key)
        if item is None:
            print(f"  {DIM}{key:<42}{RESET}  {RED}{'not found on host':<22}{RESET}  {'—':<12}  {RED}MISSING{RESET}")
            continue

        last_clock = item.get("lastclock", "0")
        last_value = item.get("lastvalue", "")
        state      = int(item.get("state", 0))   # 0=normal, 1=unsupported
        err        = item.get("error", "")
        vtype      = int(item.get("value_type", 0))

        has_data = last_clock != "0" and last_value != ""

        if state == 1:
            errors += 1
            status_str = f"{RED}UNSUPPORTED{RESET}"
            val_str    = f"{RED}{(err[:20] if err else 'see Zabbix UI')}{RESET}"
            age_str    = "—"
        elif has_data:
            received += 1
            status_str = f"{GREEN}OK{RESET}"
            val_str    = _format_value(key, last_value, vtype)
            age_str    = _age(last_clock)
        else:
            status_str = f"{YELLOW}NO DATA YET{RESET}"
            val_str    = "—"
            age_str    = "—"

        print(f"  {key:<42}  {val_str:<22}  {age_str:<12}  {status_str}")

    # -- also show LLD-discovered item prototypes that have data --------------
    proto_items = [
        i for i in items_raw
        if i["key_"] not in items_by_key or i["key_"] not in EXPECTED_KEYS
    ]
    lld_with_data = [i for i in proto_items if i.get("lastclock", "0") != "0"]

    if lld_with_data:
        print(f"\n{BOLD}  Discovered (LLD) items with data:{RESET}")
        print(f"  {DIM}{'KEY':<42} {'VALUE':<22} {'AGE'}{RESET}")
        print("  " + "─" * 80)
        for item in sorted(lld_with_data, key=lambda x: x["key_"])[:20]:
            vtype  = int(item.get("value_type", 0))
            val_str = _format_value(item["key_"], item.get("lastvalue", ""), vtype)
            print(f"  {item['key_']:<42}  {val_str:<22}  {_age(item['lastclock'])}")
        if len(lld_with_data) > 20:
            print(f"  {DIM}… and {len(lld_with_data) - 20} more{RESET}")

    # -- summary ---------------------------------------------------------------
    print(f"\n{'─' * 90}")
    ok_pct = int(received / total * 100) if total else 0
    if received == total:
        colour = GREEN
        icon = "✔"
    elif received >= total // 2:
        colour = YELLOW
        icon = "⚠"
    else:
        colour = RED
        icon = "✗"

    print(
        f"  {colour}{BOLD}{icon}  {received}/{total} items delivering data  "
        f"({ok_pct}%)  |  {errors} unsupported{RESET}"
    )
    if errors:
        print(f"  {YELLOW}Hint: unsupported items often need a restart of zabbix_agentd or a "
              f"different Zabbix agent version.{RESET}")
    print()

    return 0 if received >= total // 2 else 1


# ── Entry point ────────────────────────────────────────────────────────────────

def _load_env() -> dict[str, str]:
    env_file = Path(".env")
    if env_file.exists():
        merged = {**os.environ, **dotenv_values(env_file)}
    else:
        merged = dict(os.environ)
    return {k: str(v) for k, v in merged.items() if v is not None}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify live Zabbix check values")
    parser.add_argument("--host", default="zabbixtest3100",
                        help="Zabbix host to query (default: zabbixtest3100)")
    parser.add_argument("--template", default="system-health",
                        help="Template name to verify (default: system-health)")
    parser.add_argument("--wait", type=int, default=0,
                        help="Seconds to wait for data before reporting (default: 0)")
    args = parser.parse_args()

    env = _load_env()
    return verify(args.host, args.template, args.wait, env)


if __name__ == "__main__":
    sys.exit(main())
