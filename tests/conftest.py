"""Shared fixtures for zbx end-to-end integration tests.

All tests connect to the real Zabbix instance configured in .env.
Templates created during tests are isolated under a unique prefix and
cleaned up automatically in fixture teardown.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import yaml

from zbx.config_loader import ConfigLoader
from zbx.zabbix_client import ZabbixClient

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
ENV_FILE = REPO_ROOT / ".env"


# ---------------------------------------------------------------------------
# Skip entire session if Zabbix is not reachable
# ---------------------------------------------------------------------------

def _zabbix_reachable() -> bool:
    """Return True if the Zabbix API responds (used to skip e2e tests in CI)."""
    import urllib.request  # noqa: PLC0415
    import urllib.error    # noqa: PLC0415
    import json            # noqa: PLC0415

    # Allow env-var override (set by the compat CI workflow)
    url = os.environ.get("ZBX_URL", "")
    if not url:
        try:
            cfg = ConfigLoader()
            settings = cfg.load_settings(ENV_FILE)
            url = settings.url
        except Exception:
            return False

    endpoint = url.rstrip("/") + "/api_jsonrpc.php"
    payload = json.dumps({"jsonrpc": "2.0", "method": "apiinfo.version", "params": [], "id": 1}).encode()
    try:
        req = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return "result" in json.loads(r.read())
    except Exception:
        return False


_ZABBIX_AVAILABLE = _zabbix_reachable()

# Applied automatically to test_e2e.py — skip everything if Zabbix is down
zabbix_required = pytest.mark.skipif(
    not _ZABBIX_AVAILABLE,
    reason="Zabbix API not reachable — set ZBX_URL / ZBX_USER / ZBX_PASSWORD or start Zabbix",
)


@pytest.fixture(scope="session")
def settings():
    cfg = ConfigLoader()
    return cfg.load_settings(ENV_FILE)


@pytest.fixture(scope="session")
def client(settings):
    c = ZabbixClient(settings)
    c.login()
    return c


# ---------------------------------------------------------------------------
# Per-test template name / YAML helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def unique_name():
    """Return a unique template technical name for this test run."""
    return f"zbx-e2e-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def template_cleanup(client):
    """Accumulate template names; delete them all on teardown."""
    created: list[str] = []

    def register(name: str) -> None:
        created.append(name)

    yield register

    # Teardown: delete every template registered during the test
    for name in created:
        matches = client.find_templates(name)
        if matches:
            ids = [m["templateid"] for m in matches]
            try:
                client._call("template.delete", ids)
            except Exception:
                pass  # already gone


@pytest.fixture()
def tmp_yaml(tmp_path):
    """Return a helper that writes a dict to a YAML file and gives back its path."""
    def _write(data: dict, filename: str = "template.yaml") -> Path:
        p = tmp_path / filename
        p.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False))
        return p
    return _write


def minimal_template(name: str) -> dict:
    """Return a minimal valid template dict."""
    return {
        "template": name,
        "name": f"E2E Test {name}",
        "description": "Created by zbx integration tests",
        "groups": ["Templates"],
        "items": [
            {
                "name": "Agent ping",
                "key": "agent.ping",
                "interval": "60s",
                "value_type": "unsigned",
            }
        ],
        "triggers": [
            {
                "name": "Agent unreachable",
                "expression": f"last(/{name}/agent.ping)=0",
                "severity": "high",
            }
        ],
        "discovery_rules": [],
    }


def rich_template(name: str) -> dict:
    """Full template with dependent items, calculated items, and dependent LLD rule."""
    return {
        "template": name,
        "name": f"E2E Rich {name}",
        "description": "Full feature template",
        "groups": ["Templates"],
        "items": [
            {
                "name": "Raw CPU data",
                "key": "system.cpu.util[percpu,idle]",
                "interval": "60s",
                "units": "%",
                "tags": [{"tag": "component", "value": "cpu"}],
            },
            {
                "name": "CPU utilization",
                "key": "system.cpu.util",
                "interval": "0",
                "type": "dependent",
                "master_item_key": "system.cpu.util[percpu,idle]",
                "units": "%",
                "history": "31d",
                "tags": [{"tag": "component", "value": "cpu"}],
            },
            {
                "name": "CPU load per CPU",
                "key": "system.cpu.load.percpu",
                "interval": "60s",
                "type": "calculated",
                "params": f"last(/{name}/system.cpu.util) / 100",
                "tags": [{"tag": "component", "value": "cpu"}],
            },
            {
                "name": "Get filesystems",
                "key": "vfs.fs.get",
                "interval": "1m",
                "value_type": "text",
                "trends": "0",
                "tags": [{"tag": "component", "value": "filesystem"}],
            },
        ],
        "triggers": [
            {
                "name": "High CPU utilization",
                "expression": f"avg(/{name}/system.cpu.util,5m) > 80",
                "severity": "high",
                "tags": [{"tag": "scope", "value": "performance"}],
            }
        ],
        "discovery_rules": [
            {
                "name": "Network interface discovery",
                "key": "net.if.discovery",
                "interval": "1h",
                "filter": [{"macro": "{#IFNAME}", "value": ".*"}],
                "item_prototypes": [
                    {
                        "name": "{#IFNAME}: Incoming traffic",
                        "key": "net.if.in[{#IFNAME}]",
                        "interval": "60s",
                        "value_type": "unsigned",
                        "units": "bps",
                    },
                    {
                        "name": "{#IFNAME}: Outgoing traffic",
                        "key": "net.if.out[{#IFNAME}]",
                        "interval": "0",
                        "type": "dependent",
                        "master_item_key": "net.if.in[{#IFNAME}]",
                        "value_type": "unsigned",
                        "units": "bps",
                    },
                    {
                        "name": "{#IFNAME}: Total traffic",
                        "key": "net.if.total[{#IFNAME}]",
                        "interval": "60s",
                        "type": "calculated",
                        "params": f"last(/{name}/net.if.in[{{#IFNAME}}]) + last(/{name}/net.if.out[{{#IFNAME}}])",
                        "value_type": "unsigned",
                        "units": "bps",
                    },
                ],
                "trigger_prototypes": [
                    {
                        "name": "{#IFNAME}: High traffic",
                        "expression": f"avg(/{name}/net.if.in[{{#IFNAME}}],5m) > 100000000",
                        "severity": "warning",
                    }
                ],
            },
            {
                "name": "Mounted filesystem discovery",
                "key": "vfs.fs.dependent.discovery",
                "interval": "0",
                "type": "dependent",
                "master_item_key": "vfs.fs.get",
                "filter": [
                    {"macro": "{#FSTYPE}", "value": "ext4"},
                    {"macro": "{#FSTYPE}", "value": "xfs"},
                ],
                "item_prototypes": [
                    {
                        "name": "{#FSNAME}: Used space",
                        "key": "vfs.fs.size[{#FSNAME},used]",
                        "interval": "60s",
                        "value_type": "unsigned",
                        "units": "B",
                    },
                    {
                        "name": "{#FSNAME}: Free space",
                        "key": "vfs.fs.size[{#FSNAME},free]",
                        "interval": "60s",
                        "value_type": "unsigned",
                        "units": "B",
                    },
                ],
            },
        ],
    }
