#!/usr/bin/env python3
"""Zabbix UserParameter script — HAProxy monitoring via stats socket/HTTP.

Usage (zabbix_agentd.d):
    UserParameter=haproxy.stat[*],/usr/local/zbx/scripts/check_haproxy.py $1

Supported keys:
    haproxy.stat[ping]                 — 1 if stats page reachable, 0 otherwise
    haproxy.stat[active_frontends]     — number of FRONTEND proxies UP
    haproxy.stat[active_backends]      — number of BACKEND proxies UP
    haproxy.stat[active_servers]       — number of active servers (UP)
    haproxy.stat[sessions_current]     — current sessions across all frontends
    haproxy.stat[sessions_max]         — max sessions recorded
    haproxy.stat[requests_total]       — total HTTP requests (hrsp_* sum)
    haproxy.stat[errors_conn]          — connection errors (econ)
    haproxy.stat[errors_resp]          — response errors (eresp)
    haproxy.stat[down_servers]         — number of DOWN servers

Environment variables:
    HAPROXY_STATS_URL  (default: http://127.0.0.1:8404/stats;csv)
    HAPROXY_USER       (default: "")
    HAPROXY_PASSWORD   (default: "")
"""

from __future__ import annotations

import csv
import io
import os
import sys
import urllib.request
import urllib.error
import base64

STATS_URL = os.getenv("HAPROXY_STATS_URL", "http://127.0.0.1:8404/stats;csv")
USER = os.getenv("HAPROXY_USER", "")
PASSWORD = os.getenv("HAPROXY_PASSWORD", "")


def _fetch_csv() -> list[dict]:
    req = urllib.request.Request(STATS_URL)
    if USER:
        creds = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read().decode()
    # HAProxy CSV has a leading '# ' on the header line
    raw = raw.lstrip("# ")
    reader = csv.DictReader(io.StringIO(raw))
    return list(reader)


def _int(val: str) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def main(key: str) -> str:
    if key == "ping":
        try:
            _fetch_csv()
            return "1"
        except Exception:
            return "0"

    try:
        rows = _fetch_csv()
    except Exception as exc:
        return f"ERROR: {exc}"

    if key == "active_frontends":
        return str(sum(1 for r in rows if r.get("svname") == "FRONTEND" and r.get("status") == "OPEN"))
    if key == "active_backends":
        return str(sum(1 for r in rows if r.get("svname") == "BACKEND" and r.get("status") == "UP"))
    if key == "active_servers":
        return str(sum(1 for r in rows if r.get("svname") not in ("FRONTEND", "BACKEND") and r.get("status") == "UP"))
    if key == "down_servers":
        return str(sum(1 for r in rows if r.get("svname") not in ("FRONTEND", "BACKEND") and r.get("status") == "DOWN"))
    if key == "sessions_current":
        return str(sum(_int(r.get("scur", "0")) for r in rows if r.get("svname") == "FRONTEND"))
    if key == "sessions_max":
        return str(max((_int(r.get("smax", "0")) for r in rows if r.get("svname") == "FRONTEND"), default=0))
    if key == "requests_total":
        return str(sum(_int(r.get("req_tot", "0")) for r in rows if r.get("svname") == "FRONTEND"))
    if key == "errors_conn":
        return str(sum(_int(r.get("econ", "0")) for r in rows))
    if key == "errors_resp":
        return str(sum(_int(r.get("eresp", "0")) for r in rows))

    return f"ERROR: unknown key '{key}'"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: check_haproxy.py <key>", file=sys.stderr)
        sys.exit(1)
    print(main(sys.argv[1]))
