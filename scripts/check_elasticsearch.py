#!/usr/bin/env python3
"""Zabbix UserParameter script — Elasticsearch monitoring via REST API.

Usage (zabbix_agentd.d):
    UserParameter=elasticsearch.stat[*],/usr/local/zbx/scripts/check_elasticsearch.py $1

Supported keys:
    elasticsearch.stat[ping]                   — 1 if API reachable, 0 otherwise
    elasticsearch.stat[cluster_status]         — 0=green, 1=yellow, 2=red
    elasticsearch.stat[active_primary_shards]  — active primary shard count
    elasticsearch.stat[active_shards]          — total active shards
    elasticsearch.stat[relocating_shards]      — shards being relocated
    elasticsearch.stat[unassigned_shards]      — unassigned shard count
    elasticsearch.stat[number_of_nodes]        — nodes in cluster
    elasticsearch.stat[jvm_heap_used_percent]  — JVM heap used on local node (%)
    elasticsearch.stat[docs_count]             — total documents across all indices
    elasticsearch.stat[search_query_time_ms]   — cumulative search time (ms)

Environment variables:
    ES_HOST      (default: 127.0.0.1)
    ES_PORT      (default: 9200)
    ES_USER      (default: "")
    ES_PASSWORD  (default: "")
    ES_SCHEME    (default: http)
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
import urllib.error

HOST = os.getenv("ES_HOST", "127.0.0.1")
PORT = int(os.getenv("ES_PORT", "9200"))
USER = os.getenv("ES_USER", "")
PASSWORD = os.getenv("ES_PASSWORD", "")
SCHEME = os.getenv("ES_SCHEME", "http")
BASE_URL = f"{SCHEME}://{HOST}:{PORT}"


def _get(path: str) -> dict:
    req = urllib.request.Request(f"{BASE_URL}{path}")
    if USER:
        creds = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def main(key: str) -> str:
    if key == "ping":
        try:
            _get("/")
            return "1"
        except Exception:
            return "0"

    try:
        if key in ("cluster_status", "active_primary_shards", "active_shards",
                   "relocating_shards", "unassigned_shards", "number_of_nodes"):
            data = _get("/_cluster/health")
            status_map = {"green": 0, "yellow": 1, "red": 2}
            field_map = {
                "cluster_status":       ("status", lambda v: status_map.get(v, 2)),
                "active_primary_shards": ("active_primary_shards", int),
                "active_shards":         ("active_shards", int),
                "relocating_shards":     ("relocating_shards", int),
                "unassigned_shards":     ("unassigned_shards", int),
                "number_of_nodes":       ("number_of_nodes", int),
            }
            field, transform = field_map[key]
            return str(transform(data.get(field, 0)))

        if key == "jvm_heap_used_percent":
            data = _get("/_nodes/_local/stats/jvm")
            nodes = data.get("nodes", {})
            if not nodes:
                return "0"
            node = next(iter(nodes.values()))
            return str(node["jvm"]["mem"]["heap_used_percent"])

        if key in ("docs_count", "search_query_time_ms"):
            data = _get("/_stats/docs,search")
            totals = data.get("_all", {}).get("total", {})
            if key == "docs_count":
                return str(totals.get("docs", {}).get("count", 0))
            if key == "search_query_time_ms":
                return str(totals.get("search", {}).get("query_time_in_millis", 0))

        return f"ERROR: unknown key '{key}'"
    except Exception as exc:
        return f"ERROR: {exc}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: check_elasticsearch.py <key>", file=sys.stderr)
        sys.exit(1)
    print(main(sys.argv[1]))
