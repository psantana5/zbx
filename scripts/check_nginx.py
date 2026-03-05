#!/usr/bin/env python3
"""
check_nginx.py — Zabbix UserParameter script for Nginx monitoring.

Uses the stub_status module (add to nginx.conf):
  location /nginx_status {
      stub_status;
      allow 127.0.0.1;
      deny all;
  }

Usage (add to Zabbix agent config):
  UserParameter=nginx.stat[*],/usr/local/zbx/scripts/check_nginx.py $1

Supported keys:
  nginx.stat[ping]              — 1 if reachable, 0 otherwise
  nginx.stat[active]            — active connections
  nginx.stat[accepts]           — total accepted connections
  nginx.stat[handled]           — total handled connections
  nginx.stat[requests]          — total requests
  nginx.stat[reading]           — connections in reading state
  nginx.stat[writing]           — connections in writing state
  nginx.stat[waiting]           — connections in waiting state

Requirements:
  pip install requests

Environment:
  NGINX_STATUS_URL    default http://localhost/nginx_status
"""
from __future__ import annotations

import os
import re
import sys

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

URL = os.environ.get("NGINX_STATUS_URL", "http://localhost/nginx_status")


def _get_stats() -> dict[str, int]:
    resp = requests.get(URL, timeout=5)
    resp.raise_for_status()
    body = resp.text

    # Active connections: 42
    # server accepts handled requests
    #  1234 1234 5678
    # Reading: 0 Writing: 1 Waiting: 10
    m_active   = re.search(r"Active connections:\s+(\d+)", body)
    m_totals   = re.search(r"(\d+)\s+(\d+)\s+(\d+)", body)
    m_rw       = re.search(r"Reading:\s+(\d+)\s+Writing:\s+(\d+)\s+Waiting:\s+(\d+)", body)

    return {
        "active":   int(m_active.group(1))   if m_active  else 0,
        "accepts":  int(m_totals.group(1))   if m_totals  else 0,
        "handled":  int(m_totals.group(2))   if m_totals  else 0,
        "requests": int(m_totals.group(3))   if m_totals  else 0,
        "reading":  int(m_rw.group(1))       if m_rw      else 0,
        "writing":  int(m_rw.group(2))       if m_rw      else 0,
        "waiting":  int(m_rw.group(3))       if m_rw      else 0,
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: check_nginx.py <key>")
        sys.exit(1)

    key = sys.argv[1]

    try:
        if key == "ping":
            resp = requests.get(URL, timeout=5)
            print(1 if resp.status_code == 200 else 0)
            return

        stats = _get_stats()
        if key in stats:
            print(stats[key])
        else:
            print(f"ERROR: unknown key '{key}'")
            sys.exit(1)

    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
