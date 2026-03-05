#!/usr/bin/env python3
"""
check_redis.py — Zabbix UserParameter script for Redis monitoring.

Usage (add to Zabbix agent config):
  UserParameter=redis.stat[*],/usr/local/zbx/scripts/check_redis.py $1

Supported keys:
  redis.stat[ping]              — 1 if reachable, 0 otherwise
  redis.stat[connected_clients] — number of connected clients
  redis.stat[used_memory]       — used memory in bytes
  redis.stat[used_memory_rss]   — RSS memory in bytes
  redis.stat[hit_rate]          — keyspace hit rate (%)
  redis.stat[evicted_keys]      — total evicted keys
  redis.stat[ops_per_sec]       — instantaneous ops/sec
  redis.stat[replication.lag]   — replication offset lag in bytes
  redis.stat[keyspace.keys]     — total keys across all databases

Requirements:
  pip install redis

Environment:
  REDIS_HOST    default localhost
  REDIS_PORT    default 6379
  REDIS_PASS    default ''
  REDIS_DB      default 0
"""
from __future__ import annotations

import os
import sys

try:
    import redis
except ImportError:
    print("ERROR: redis-py not installed. Run: pip install redis")
    sys.exit(1)

HOST = os.environ.get("REDIS_HOST", "localhost")
PORT = int(os.environ.get("REDIS_PORT", "6379"))
PASS = os.environ.get("REDIS_PASS") or None
DB   = int(os.environ.get("REDIS_DB", "0"))


def _client():
    return redis.Redis(host=HOST, port=PORT, password=PASS, db=DB, socket_timeout=5, decode_responses=True)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: check_redis.py <key>")
        sys.exit(1)

    key = sys.argv[1]

    try:
        r = _client()
        info = r.info()       # "all" sections by default in redis-py
        info_repl = r.info("replication")

        if key == "ping":
            print(1 if r.ping() else 0)

        elif key == "connected_clients":
            print(info.get("connected_clients", 0))

        elif key == "used_memory":
            print(info.get("used_memory", 0))

        elif key == "used_memory_rss":
            print(info.get("used_memory_rss", 0))

        elif key == "hit_rate":
            hits   = int(info.get("keyspace_hits", 0))
            misses = int(info.get("keyspace_misses", 0))
            total  = hits + misses
            rate   = round((hits / total * 100), 2) if total else 100.0
            print(rate)

        elif key == "evicted_keys":
            print(info.get("evicted_keys", 0))

        elif key == "ops_per_sec":
            print(info.get("instantaneous_ops_per_sec", 0))

        elif key == "replication.lag":
            # master_repl_offset - slave_repl_offset (on replica); 0 on master
            if info_repl.get("role") == "slave":
                master = int(info_repl.get("master_repl_offset", 0))
                replica = int(info_repl.get("slave_repl_offset", 0))
                print(max(0, master - replica))
            else:
                print(0)

        elif key == "keyspace.keys":
            total = 0
            for v in r.info("keyspace").values():
                if isinstance(v, dict):
                    total += int(v.get("keys", 0))
            print(total)

        else:
            print(f"ERROR: unknown key '{key}'")
            sys.exit(1)

    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
