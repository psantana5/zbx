#!/usr/bin/env python3
"""
check_mongodb.py — Zabbix UserParameter script for MongoDB monitoring.

Usage (add to Zabbix agent config):
  UserParameter=mongodb.stat[*],python3 /usr/local/zbx/scripts/check_mongodb.py $1

Supported keys:
  mongodb.stat[ping]                    — 1 if port 27017 is reachable, 0 otherwise
  mongodb.stat[connections.active]      — active client connections
  mongodb.stat[connections.available]   — available (unused) connections
  mongodb.stat[opcounters.total]        — sum of all opcounters (insert+query+update+delete+getmore+command)
  mongodb.stat[mem.resident]            — resident memory in MB
  mongodb.stat[mem.virtual]             — virtual memory in MB
  mongodb.stat[extra_info.page_faults]  — page faults per second
  mongodb.stat[repl.lag]                — replication lag in seconds (0 if primary/standalone)

Strategy:
  1. Try to get full serverStatus by running:
       mongosh --quiet --eval "JSON.stringify(db.serverStatus())"
  2. Fall back to mongosh legacy shell:
       mongo --quiet --eval "JSON.stringify(db.serverStatus())"
  3. For ping only: TCP connect to localhost:27017
  4. If mongosh unavailable, return 0 for all stats except ping.

Environment:
  MONGODB_HOST   default localhost
  MONGODB_PORT   default 27017
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from typing import Any

HOST = os.environ.get("MONGODB_HOST", "localhost")
PORT = int(os.environ.get("MONGODB_PORT", "27017"))


def tcp_ping(host: str, port: int, timeout: float = 3.0) -> bool:
    """Return True if a TCP connection to host:port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def run_mongosh(eval_expr: str, timeout: int = 10) -> str | None:
    """Try mongosh then mongo shell; return stdout or None on failure."""
    for binary in ("mongosh", "mongo"):
        try:
            result = subprocess.run(
                [binary, "--quiet", "--eval", eval_expr],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return None


def get_server_status() -> dict[str, Any] | None:
    """Return parsed serverStatus document or None."""
    out = run_mongosh('JSON.stringify(db.serverStatus())')
    if out is None:
        return None
    # mongosh may emit ANSI codes or extra lines before the JSON blob
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    # Try whole output as JSON
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def get_value(key: str) -> str:
    """Resolve a mongodb.stat key to a printable value."""
    if key == "ping":
        return "1" if tcp_ping(HOST, PORT) else "0"

    status = get_server_status()
    if status is None:
        # No mongosh available — return 0 for all numeric metrics
        return "0"

    try:
        if key == "connections.active":
            return str(status["connections"].get("active", 0))

        if key == "connections.available":
            return str(status["connections"].get("available", 0))

        if key == "opcounters.total":
            ops = status.get("opcounters", {})
            total = sum(
                ops.get(k, 0)
                for k in ("insert", "query", "update", "delete", "getmore", "command")
            )
            return str(total)

        if key == "mem.resident":
            return str(status.get("mem", {}).get("resident", 0))

        if key == "mem.virtual":
            return str(status.get("mem", {}).get("virtual", 0))

        if key == "extra_info.page_faults":
            return str(status.get("extra_info", {}).get("page_faults", 0))

        if key == "repl.lag":
            # replicationInfo is not always present; return 0 if unavailable
            repl = status.get("repl", {})
            # lag is only meaningful on secondaries
            lag = repl.get("lag", 0)
            return str(lag if lag is not None else 0)

    except (KeyError, TypeError):
        return "0"

    return "0"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: check_mongodb.py <key>", file=sys.stderr)
        sys.exit(1)

    key = sys.argv[1]
    try:
        print(get_value(key))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        print("0")


if __name__ == "__main__":
    main()
