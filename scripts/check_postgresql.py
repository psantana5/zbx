#!/usr/bin/env python3
"""
check_postgresql.py — Zabbix UserParameter script for PostgreSQL monitoring.

Usage (add to Zabbix agent config):
  UserParameter=postgresql.stat[*],/usr/local/zbx/scripts/check_postgresql.py $1

Supported keys:
  postgresql.stat[connections.active]    — active client connections
  postgresql.stat[connections.idle]      — idle client connections
  postgresql.stat[db.size,<dbname>]      — database size in bytes
  postgresql.stat[db.deadlocks,<dbname>] — deadlocks in database
  postgresql.stat[db.commits,<dbname>]   — transactions committed
  postgresql.stat[db.rollbacks,<dbname>] — transactions rolled back
  postgresql.stat[replication.lag]       — replication lag in bytes (primary only)
  postgresql.stat[ping]                  — 1 if reachable, 0 otherwise

Requirements:
  pip install psycopg2-binary

Environment / args:
  PG_HOST    default localhost
  PG_PORT    default 5432
  PG_USER    default postgres
  PG_PASS    default ''
  PG_DB      default postgres
"""
from __future__ import annotations

import os
import sys

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

HOST = os.environ.get("PG_HOST", "localhost")
PORT = int(os.environ.get("PG_PORT", "5432"))
USER = os.environ.get("PG_USER", "postgres")
PASS = os.environ.get("PG_PASS", "")
DB   = os.environ.get("PG_DB",   "postgres")


def _connect(dbname: str = DB):
    return psycopg2.connect(host=HOST, port=PORT, user=USER, password=PASS, dbname=dbname, connect_timeout=5)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: check_postgresql.py <key> [arg]")
        sys.exit(1)

    key  = sys.argv[1]
    arg  = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        if key == "ping":
            conn = _connect()
            conn.close()
            print(1)

        elif key == "connections.active":
            with _connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state='active';")
                print(cur.fetchone()[0])

        elif key == "connections.idle":
            with _connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state='idle';")
                print(cur.fetchone()[0])

        elif key == "db.size":
            db = arg or DB
            with _connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_database_size(%s);", (db,))
                print(cur.fetchone()[0])

        elif key == "db.deadlocks":
            db = arg or DB
            with _connect(db) as conn, conn.cursor() as cur:
                cur.execute("SELECT deadlocks FROM pg_stat_database WHERE datname=%s;", (db,))
                row = cur.fetchone()
                print(row[0] if row else 0)

        elif key == "db.commits":
            db = arg or DB
            with _connect(db) as conn, conn.cursor() as cur:
                cur.execute("SELECT xact_commit FROM pg_stat_database WHERE datname=%s;", (db,))
                row = cur.fetchone()
                print(row[0] if row else 0)

        elif key == "db.rollbacks":
            db = arg or DB
            with _connect(db) as conn, conn.cursor() as cur:
                cur.execute("SELECT xact_rollback FROM pg_stat_database WHERE datname=%s;", (db,))
                row = cur.fetchone()
                print(row[0] if row else 0)

        elif key == "replication.lag":
            with _connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT CASE WHEN pg_is_in_recovery() THEN "
                    "pg_wal_lsn_diff(pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn()) "
                    "ELSE 0 END;"
                )
                print(cur.fetchone()[0] or 0)

        else:
            print(f"ERROR: unknown key '{key}'")
            sys.exit(1)

    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
