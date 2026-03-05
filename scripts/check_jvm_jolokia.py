#!/usr/bin/env python3
"""
check_jvm_jolokia.py — Zabbix UserParameter script for JVM monitoring via Jolokia.

Usage (add to Zabbix agent config):
  UserParameter=jvm.jolokia[*],python3 /usr/local/zbx/scripts/check_jvm_jolokia.py $1

Supported keys:
  jvm.jolokia[ping]            — 1 if Jolokia responds, 0 otherwise
  jvm.jolokia[heap.used]       — heap used in MB
  jvm.jolokia[heap.max]        — heap max in MB
  jvm.jolokia[heap.usage_pct]  — heap used / max * 100 (%)
  jvm.jolokia[gc.old.count]    — old-gen GC collection count
  jvm.jolokia[gc.old.time]     — old-gen GC total time (ms)
  jvm.jolokia[gc.young.count]  — young-gen GC collection count
  jvm.jolokia[threads.count]   — live thread count
  jvm.jolokia[classes.loaded]  — loaded class count

Environment:
  JOLOKIA_HOST   default localhost
  JOLOKIA_PORT   default 8778
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

HOST = os.environ.get("JOLOKIA_HOST", "localhost")
PORT = int(os.environ.get("JOLOKIA_PORT", "8778"))
BASE_URL = f"http://{HOST}:{PORT}/jolokia"
TIMEOUT = 5

# GC collector name candidates for old and young generation
_GC_OLD_NAMES = ("G1 Old Gen", "ConcurrentMarkSweep", "PS MarkSweep", "Tenured Gen")
_GC_YOUNG_NAMES = ("G1 Young Generation", "ParNew", "PS Scavenge", "Copy")


def jolokia_get(path: str) -> dict[str, Any] | None:
    """Perform a GET request to the Jolokia endpoint; return parsed JSON or None."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("status") == 200:
                return data
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        pass
    return None


def read_mbean(mbean: str, attribute: str) -> Any | None:
    """Read a single MBean attribute; return the value or None on error."""
    path = f"read/{urllib.parse.quote(mbean, safe='')}/{attribute}"
    data = jolokia_get(path)
    if data is not None:
        return data.get("value")
    return None


def _mb(value: int | float) -> int:
    """Convert bytes to MB (integer)."""
    return int(value // (1024 * 1024))


def _find_gc(names: tuple[str, ...]) -> dict[str, Any] | None:
    """Try each GC collector name and return the first successful Jolokia read."""
    import urllib.parse  # noqa: PLC0415 (import inside function is fine here)
    for name in names:
        mbean = f"java.lang:type=GarbageCollector,name={name}"
        encoded = urllib.parse.quote(mbean, safe="")
        data = jolokia_get(f"read/{encoded}/CollectionCount,CollectionTime")
        if data is not None and data.get("value"):
            return data["value"]
    return None


def get_value(key: str) -> str:
    import urllib.parse  # noqa: PLC0415

    if key == "ping":
        data = jolokia_get("")
        return "1" if data is not None else "0"

    if key in ("heap.used", "heap.max", "heap.usage_pct"):
        mbean = "java.lang:type=Memory"
        encoded = urllib.parse.quote(mbean, safe="")
        data = jolokia_get(f"read/{encoded}/HeapMemoryUsage")
        if data is None:
            return "0"
        heap = data.get("value", {})
        used = heap.get("used", 0)
        maximum = heap.get("max", 0)
        if key == "heap.used":
            return str(_mb(used))
        if key == "heap.max":
            return str(_mb(maximum))
        if key == "heap.usage_pct":
            if maximum and maximum > 0:
                return f"{used / maximum * 100:.2f}"
            return "0"

    if key in ("gc.old.count", "gc.old.time"):
        gc = _find_gc(_GC_OLD_NAMES)
        if gc is None:
            return "0"
        if key == "gc.old.count":
            return str(gc.get("CollectionCount", 0))
        return str(gc.get("CollectionTime", 0))

    if key == "gc.young.count":
        gc = _find_gc(_GC_YOUNG_NAMES)
        if gc is None:
            return "0"
        return str(gc.get("CollectionCount", 0))

    if key == "threads.count":
        mbean = "java.lang:type=Threading"
        encoded = urllib.parse.quote(mbean, safe="")
        data = jolokia_get(f"read/{encoded}/ThreadCount")
        if data is None:
            return "0"
        return str(data.get("value", 0))

    if key == "classes.loaded":
        mbean = "java.lang:type=ClassLoading"
        encoded = urllib.parse.quote(mbean, safe="")
        data = jolokia_get(f"read/{encoded}/LoadedClassCount")
        if data is None:
            return "0"
        return str(data.get("value", 0))

    return "0"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: check_jvm_jolokia.py <key>", file=sys.stderr)
        sys.exit(1)

    key = sys.argv[1]
    try:
        print(get_value(key))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        print("0")


if __name__ == "__main__":
    main()
