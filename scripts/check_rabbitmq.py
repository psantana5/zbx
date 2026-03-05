#!/usr/bin/env python3
"""Zabbix UserParameter script — RabbitMQ monitoring via management API.

Usage (zabbix_agentd.d):
    UserParameter=rabbitmq.stat[*],/usr/local/zbx/scripts/check_rabbitmq.py $1

Supported keys:
    rabbitmq.stat[ping]                — 1 if API reachable, 0 otherwise
    rabbitmq.stat[messages_ready]      — messages ready in all queues
    rabbitmq.stat[messages_unacked]    — messages unacknowledged
    rabbitmq.stat[connections]         — total connections
    rabbitmq.stat[consumers]           — total consumers
    rabbitmq.stat[queues]              — number of queues
    rabbitmq.stat[mem_used]            — memory used by broker (bytes)
    rabbitmq.stat[fd_used]             — file descriptors used

Environment variables:
    RABBITMQ_HOST      (default: 127.0.0.1)
    RABBITMQ_PORT      (default: 15672)   management API port
    RABBITMQ_USER      (default: guest)
    RABBITMQ_PASSWORD  (default: guest)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
import base64

HOST = os.getenv("RABBITMQ_HOST", "127.0.0.1")
PORT = int(os.getenv("RABBITMQ_PORT", "15672"))
USER = os.getenv("RABBITMQ_USER", "guest")
PASSWORD = os.getenv("RABBITMQ_PASSWORD", "guest")
BASE_URL = f"http://{HOST}:{PORT}/api"


def _get(path: str) -> dict:
    url = f"{BASE_URL}{path}"
    creds = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {creds}"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def main(key: str) -> str:
    if key == "ping":
        try:
            _get("/healthchecks/node")
            return "1"
        except Exception:
            return "0"

    try:
        if key in ("messages_ready", "messages_unacked", "connections", "consumers", "queues"):
            data = _get("/overview")
            mapping = {
                "messages_ready":   ("queue_totals", "messages_ready"),
                "messages_unacked": ("queue_totals", "messages_unacknowledged"),
                "connections":      ("object_totals", "connections"),
                "consumers":        ("object_totals", "consumers"),
                "queues":           ("object_totals", "queues"),
            }
            section, field = mapping[key]
            return str(data.get(section, {}).get(field, 0))

        if key in ("mem_used", "fd_used"):
            nodes = _get("/nodes")
            if not nodes:
                return "0"
            node = nodes[0]
            mapping = {"mem_used": "mem_used", "fd_used": "fd_used"}
            return str(node.get(mapping[key], 0))

        return f"ERROR: unknown key '{key}'"
    except Exception as exc:
        return f"ERROR: {exc}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: check_rabbitmq.py <key>", file=sys.stderr)
        sys.exit(1)
    print(main(sys.argv[1]))
