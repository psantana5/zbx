#!/usr/bin/env python3
"""Zabbix UserParameter script — Kubernetes node monitoring via Kubelet API.

Usage (zabbix_agentd.d):
    UserParameter=k8s.node[*],/usr/local/zbx/scripts/check_k8s_node.py $1

Supported keys:
    k8s.node[ping]                   — 1 if kubelet metrics reachable, 0 otherwise
    k8s.node[cpu_millicores]         — node CPU usage in millicores
    k8s.node[memory_used_bytes]      — node memory working set bytes
    k8s.node[memory_capacity_bytes]  — node memory capacity bytes
    k8s.node[pods_running]           — number of running pods
    k8s.node[pods_capacity]          — max pods the node can schedule
    k8s.node[condition_ready]        — 1 = Ready, 0 = NotReady
    k8s.node[condition_disk_pressure]    — 1 = DiskPressure, 0 = ok
    k8s.node[condition_memory_pressure]  — 1 = MemoryPressure, 0 = ok

Environment variables:
    KUBELET_HOST     (default: 127.0.0.1)
    KUBELET_PORT     (default: 10255)   read-only port (or 10250 with TLS)
    KUBELET_SCHEME   (default: http)    use https for port 10250
    KUBELET_TOKEN    (default: "")      Bearer token for authenticated port
    K8S_NODE_NAME    (default: "")      node name for summary API (auto-detected if empty)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
import socket

HOST = os.getenv("KUBELET_HOST", "127.0.0.1")
PORT = int(os.getenv("KUBELET_PORT", "10255"))
SCHEME = os.getenv("KUBELET_SCHEME", "http")
TOKEN = os.getenv("KUBELET_TOKEN", "")
NODE_NAME = os.getenv("K8S_NODE_NAME", "") or socket.gethostname()
BASE_URL = f"{SCHEME}://{HOST}:{PORT}"


def _get(path: str) -> dict:
    import ssl  # noqa: PLC0415
    req = urllib.request.Request(f"{BASE_URL}{path}")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    ctx = ssl.create_default_context() if SCHEME == "https" else None
    if ctx:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
        return json.loads(resp.read())


def main(key: str) -> str:
    if key == "ping":
        try:
            _get("/healthz")
            return "1"
        except Exception:
            return "0"

    try:
        if key in ("cpu_millicores", "memory_used_bytes", "memory_capacity_bytes",
                   "pods_running", "pods_capacity"):
            data = _get(f"/stats/summary")
            node = data.get("node", {})
            if key == "cpu_millicores":
                nano = node.get("cpu", {}).get("usageNanoCores", 0)
                return str(round(nano / 1_000_000, 2))
            if key == "memory_used_bytes":
                return str(node.get("memory", {}).get("workingSetBytes", 0))
            if key == "memory_capacity_bytes":
                # from /api/v1/nodes/<name> — fall back to /stats/summary rlimit
                return str(node.get("memory", {}).get("availableBytes", 0))
            if key == "pods_running":
                return str(len(data.get("pods", [])))
            if key == "pods_capacity":
                # capacity not available in summary; use a static env or return 110 (default)
                return os.getenv("K8S_POD_CAPACITY", "110")

        if key in ("condition_ready", "condition_disk_pressure", "condition_memory_pressure"):
            # Requires authenticated kubelet (10250) with node status access
            # Try /api/v1/nodes/<name> via kube-apiserver or return from node conditions
            try:
                apiserver = os.getenv("KUBERNETES_SERVICE_HOST", "")
                apiserver_port = os.getenv("KUBERNETES_SERVICE_PORT", "443")
                if not apiserver:
                    return "ERROR: KUBERNETES_SERVICE_HOST not set"
                token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
                with open(token_path) as f:
                    sa_token = f.read().strip()
                import ssl  # noqa: PLC0415
                req = urllib.request.Request(
                    f"https://{apiserver}:{apiserver_port}/api/v1/nodes/{NODE_NAME}",
                    headers={"Authorization": f"Bearer {sa_token}"},
                )
                ctx = ssl.create_default_context()
                ctx.load_verify_locations("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
                with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                    node_data = json.loads(resp.read())
                conditions = {c["type"]: c["status"]
                              for c in node_data.get("status", {}).get("conditions", [])}
                cond_map = {
                    "condition_ready": "Ready",
                    "condition_disk_pressure": "DiskPressure",
                    "condition_memory_pressure": "MemoryPressure",
                }
                cond = conditions.get(cond_map[key], "Unknown")
                if key == "condition_ready":
                    return "1" if cond == "True" else "0"
                else:
                    return "1" if cond == "True" else "0"
            except Exception as exc:
                return f"ERROR: {exc}"

        return f"ERROR: unknown key '{key}'"
    except Exception as exc:
        return f"ERROR: {exc}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: check_k8s_node.py <key>", file=sys.stderr)
        sys.exit(1)
    print(main(sys.argv[1]))
