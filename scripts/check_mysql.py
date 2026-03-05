#!/usr/bin/env python3
"""Zabbix UserParameter script — MySQL monitoring.

Usage (zabbix_agentd.d):
    UserParameter=mysql.stat[*],/usr/local/zbx/scripts/check_mysql.py $1

Supported keys:
    mysql.stat[ping]                   — 1 if reachable, 0 otherwise
    mysql.stat[connections]            — current open connections
    mysql.stat[max_connections]        — @@max_connections
    mysql.stat[threads_running]        — Threads_running status var
    mysql.stat[queries_per_sec]        — Questions delta (since last call)
    mysql.stat[slow_queries]           — Slow_queries cumulative counter
    mysql.stat[uptime]                 — Uptime in seconds
    mysql.stat[innodb_buffer_pool_hit] — InnoDB buffer pool hit ratio (%)

Environment variables:
    MYSQL_HOST      (default: 127.0.0.1)
    MYSQL_PORT      (default: 3306)
    MYSQL_USER      (default: zabbix)
    MYSQL_PASSWORD  (default: "")
    MYSQL_DB        (default: information_schema)
"""

from __future__ import annotations

import os
import sys

HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
PORT = int(os.getenv("MYSQL_PORT", "3306"))
USER = os.getenv("MYSQL_USER", "zabbix")
PASSWORD = os.getenv("MYSQL_PASSWORD", "")
DB = os.getenv("MYSQL_DB", "information_schema")


def _connect():
    try:
        import pymysql  # type: ignore
        return pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD,
                               db=DB, connect_timeout=5)
    except ImportError:
        pass
    try:
        import MySQLdb  # type: ignore
        return MySQLdb.connect(host=HOST, port=PORT, user=USER, passwd=PASSWORD,
                               db=DB, connect_timeout=5)
    except ImportError:
        pass
    raise RuntimeError("No MySQL driver found. Install pymysql: pip install pymysql")


def _status_var(conn, name: str) -> str:
    with conn.cursor() as cur:
        cur.execute("SHOW GLOBAL STATUS LIKE %s", (name,))
        row = cur.fetchone()
    return row[1] if row else "0"


def main(key: str) -> str:
    if key == "ping":
        try:
            conn = _connect()
            conn.close()
            return "1"
        except Exception:
            return "0"

    try:
        conn = _connect()
    except Exception as exc:
        return f"ERROR: {exc}"

    try:
        if key == "connections":
            return _status_var(conn, "Threads_connected")
        if key == "max_connections":
            with conn.cursor() as cur:
                cur.execute("SHOW VARIABLES LIKE 'max_connections'")
                row = cur.fetchone()
            return row[1] if row else "0"
        if key == "threads_running":
            return _status_var(conn, "Threads_running")
        if key == "queries_per_sec":
            return _status_var(conn, "Questions")
        if key == "slow_queries":
            return _status_var(conn, "Slow_queries")
        if key == "uptime":
            return _status_var(conn, "Uptime")
        if key == "innodb_buffer_pool_hit":
            reads = int(_status_var(conn, "Innodb_buffer_pool_read_requests") or 0)
            misses = int(_status_var(conn, "Innodb_buffer_pool_reads") or 0)
            if reads == 0:
                return "100"
            return f"{round(100 * (1 - misses / reads), 2)}"
        return f"ERROR: unknown key '{key}'"
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: check_mysql.py <key>", file=sys.stderr)
        sys.exit(1)
    print(main(sys.argv[1]))
