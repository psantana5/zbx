#!/usr/bin/env python3
"""
check_ssl.py — Zabbix UserParameter script for SSL/TLS certificate monitoring.

Usage (add to Zabbix agent config):
  UserParameter=ssl.cert[*],/usr/local/zbx/scripts/check_ssl.py $1 "$2"

Supported keys:
  ssl.cert[days_remaining,<host:port>]  — days until certificate expires (int)
  ssl.cert[issuer,<host:port>]          — certificate issuer CN
  ssl.cert[subject,<host:port>]         — certificate subject CN
  ssl.cert[valid,<host:port>]           — 1 if cert is valid today, 0 otherwise
  ssl.cert[expiry_ts,<host:port>]       — Unix timestamp of expiry date

No external dependencies — uses Python stdlib ssl module only.

Examples (manual test):
  python check_ssl.py days_remaining github.com:443
  python check_ssl.py issuer github.com:443
"""
from __future__ import annotations

import datetime
import socket
import ssl
import sys


def _get_cert(hostport: str) -> dict:  # type: ignore[type-arg]
    host, _, port_str = hostport.rpartition(":")
    port = int(port_str) if port_str else 443
    ctx = ssl.create_default_context()
    with ctx.wrap_socket(socket.create_connection((host, port), timeout=10), server_hostname=host) as ssock:
        return ssock.getpeercert()  # type: ignore[return-value]


def _days_remaining(cert: dict) -> int:  # type: ignore[type-arg]
    expiry_str = cert.get("notAfter", "")
    expiry = datetime.datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)
    delta  = expiry - datetime.datetime.now(datetime.timezone.utc)
    return max(0, delta.days)


def _issuer_cn(cert: dict) -> str:  # type: ignore[type-arg]
    for rdns in cert.get("issuer", []):
        for attr, val in rdns:
            if attr == "commonName":
                return val
    return "unknown"


def _subject_cn(cert: dict) -> str:  # type: ignore[type-arg]
    for rdns in cert.get("subject", []):
        for attr, val in rdns:
            if attr == "commonName":
                return val
    return "unknown"


def _expiry_ts(cert: dict) -> int:  # type: ignore[type-arg]
    expiry_str = cert.get("notAfter", "")
    expiry = datetime.datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)
    return int(expiry.timestamp())


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: check_ssl.py <key> <host:port>")
        sys.exit(1)

    key      = sys.argv[1]
    hostport = sys.argv[2]

    try:
        cert = _get_cert(hostport)

        if key == "days_remaining":
            print(_days_remaining(cert))

        elif key == "issuer":
            print(_issuer_cn(cert))

        elif key == "subject":
            print(_subject_cn(cert))

        elif key == "valid":
            print(1 if _days_remaining(cert) > 0 else 0)

        elif key == "expiry_ts":
            print(_expiry_ts(cert))

        else:
            print(f"ERROR: unknown key '{key}'")
            sys.exit(1)

    except ssl.SSLCertVerificationError as exc:
        print(f"ERROR: SSL verification failed: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
