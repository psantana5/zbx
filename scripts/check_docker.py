#!/usr/bin/env python3
"""
check_docker.py — Zabbix UserParameter script for Docker container monitoring.

Uses the Docker socket (or DOCKER_HOST env) via docker-py.

Usage (add to Zabbix agent config):
  UserParameter=docker.stat[*],/usr/local/zbx/scripts/check_docker.py $1 "$2"

Supported keys:
  docker.stat[ping]                     — 1 if Docker daemon is responsive
  docker.stat[containers.running]       — number of running containers
  docker.stat[containers.stopped]       — number of stopped containers
  docker.stat[containers.total]         — total containers (any state)
  docker.stat[images.total]             — total pulled images
  docker.stat[container.status,<name>]  — 1=running, 0=stopped/absent
  docker.stat[container.cpu,<name>]     — CPU usage % (last sample)
  docker.stat[container.mem,<name>]     — memory usage in bytes
  docker.stat[container.restarts,<name>]— restart count

Requirements:
  pip install docker

Notes:
  The Zabbix agent user must be in the 'docker' group:
    usermod -aG docker zabbix
"""
from __future__ import annotations

import sys

try:
    import docker
except ImportError:
    print("ERROR: docker-py not installed. Run: pip install docker")
    sys.exit(1)


def _client():
    return docker.from_env()


def _cpu_percent(stats: dict) -> float:  # type: ignore[type-arg]
    """Calculate CPU % from Docker stats dict (v1 format)."""
    cpu_delta    = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats["precpu_stats"]["cpu_usage"]["total_usage"]
    system_delta = stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
    num_cpus     = stats["cpu_stats"].get("online_cpus") or len(stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1]))
    if system_delta <= 0:
        return 0.0
    return round(cpu_delta / system_delta * num_cpus * 100.0, 2)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: check_docker.py <key> [container_name]")
        sys.exit(1)

    key  = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        c = _client()

        if key == "ping":
            print(1 if c.ping() else 0)

        elif key == "containers.running":
            print(len(c.containers.list(filters={"status": "running"})))

        elif key == "containers.stopped":
            all_c   = c.containers.list(all=True)
            stopped = [x for x in all_c if x.status != "running"]
            print(len(stopped))

        elif key == "containers.total":
            print(len(c.containers.list(all=True)))

        elif key == "images.total":
            print(len(c.images.list()))

        elif key == "container.status":
            try:
                container = c.containers.get(name)
                print(1 if container.status == "running" else 0)
            except docker.errors.NotFound:
                print(0)

        elif key == "container.cpu":
            container = c.containers.get(name)
            stats = container.stats(stream=False)
            print(_cpu_percent(stats))

        elif key == "container.mem":
            container = c.containers.get(name)
            stats = container.stats(stream=False)
            print(stats["memory_stats"].get("usage", 0))

        elif key == "container.restarts":
            container = c.containers.get(name)
            print(container.attrs["RestartCount"])

        else:
            print(f"ERROR: unknown key '{key}'")
            sys.exit(1)

    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
