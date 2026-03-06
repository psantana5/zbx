#!/usr/bin/env python3
"""
check_apache.py - Apache HTTP Server monitoring via mod_status

Usage: check_apache.py <key>

Supported keys:
  ping           - 1 if server responds, 0 otherwise
  total_accesses - Total accesses since server start
  total_kbytes   - Total kibibytes served since server start
  req_per_sec    - Average requests per second
  bytes_per_sec  - Average bytes per second
  busy_workers   - Number of busy worker threads
  idle_workers   - Number of idle worker threads
  uptime         - Server uptime in seconds

Requires Apache mod_status with ExtendedStatus On accessible at:
  http://localhost/server-status?auto
"""

import sys
import os
import urllib.request
import urllib.error

STATUS_URL = os.environ.get("APACHE_STATUS_URL", "http://localhost/server-status?auto")
TIMEOUT = int(os.environ.get("APACHE_TIMEOUT", "5"))

KEY_MAP = {
    "total_accesses": "Total Accesses",
    "total_kbytes":   "Total kBytes",
    "req_per_sec":    "ReqPerSec",
    "bytes_per_sec":  "BytesPerSec",
    "busy_workers":   "BusyWorkers",
    "idle_workers":   "IdleWorkers",
    "uptime":         "Uptime",
}


def fetch_status():
    """Fetch and parse Apache mod_status plain-text output into a dict."""
    with urllib.request.urlopen(STATUS_URL, timeout=TIMEOUT) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    data = {}
    for line in body.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            data[k.strip()] = v.strip()
    return data


def main():
    if len(sys.argv) < 2:
        print(0)
        sys.exit(1)

    key = sys.argv[1].lower()

    # ping: just check connectivity
    if key == "ping":
        try:
            fetch_status()
            print(1)
        except Exception:
            print(0)
        return

    stat_field = KEY_MAP.get(key)
    if stat_field is None:
        print(0)
        return

    try:
        data = fetch_status()
    except Exception:
        print(0)
        return

    value = data.get(stat_field)
    if value is None:
        print(0)
        return

    # Return as float for float keys, int otherwise
    try:
        if key in ("req_per_sec", "bytes_per_sec"):
            print(float(value))
        else:
            print(int(float(value)))
    except ValueError:
        print(0)


if __name__ == "__main__":
    main()
