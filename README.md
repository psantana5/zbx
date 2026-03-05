# zbx — Zabbix Configuration as Code

> Manage Zabbix templates, items, triggers and discovery rules through YAML files and Git — the same mental model as Terraform or Ansible.

```
zbx plan    configs/     # See what would change
zbx apply   configs/     # Apply changes
zbx diff    configs/     # Compare config ↔ Zabbix
zbx validate configs/    # Validate YAML schema
zbx export  linux        # Export existing template to YAML
```

---

## Why zbx?

| Pain | zbx solution |
|---|---|
| Zabbix templates live only in the web UI | Store everything as YAML in Git |
| No audit trail for monitoring changes | Every change is a commit |
| Hard to review or approve changes | PR-based workflow, just like app code |
| Drift between environments | `zbx diff` catches it |
| Can't automate template rollout | CI/CD friendly CLI |

---

## Installation

**Requirements:** Python 3.11+

```bash
# From source
git clone https://github.com/psantana5/zbx
cd zbx
pip install -e .

# Verify
zbx --version
```

---

## Quick Start

### 1. Configure credentials

```bash
cp .env.example .env
# Edit .env — set ZBX_URL, ZBX_USER, ZBX_PASSWORD
```

Or export environment variables directly:

```bash
export ZBX_URL=http://zabbix.example.com
export ZBX_USER=Admin
export ZBX_PASSWORD=secret
```

### 2. Write your first template

```yaml
# configs/templates/linux-observability.yaml

template: linux-observability
name: "Linux Observability"
description: "Core performance metrics for Linux servers"
groups:
  - Templates
  - Templates/Linux

items:
  - name: CPU utilization
    key: system.cpu.util
    interval: 30s
    value_type: float
    units: "%"

  - name: Available memory
    key: vm.memory.size[available]
    interval: 60s
    value_type: unsigned
    units: B

triggers:
  - name: High CPU utilization (>90% for 5 min)
    expression: avg(/linux-observability/system.cpu.util,5m) > 90
    severity: high
    description: "CPU usage above 90% for 5 minutes"

discovery_rules:
  - name: Mounted filesystem discovery
    key: vfs.fs.discovery
    interval: 1h
    item_prototypes:
      - name: "Filesystem {#FSNAME}: used space (%)"
        key: "vfs.fs.size[{#FSNAME},pused]"
        interval: 5m
        value_type: float
        units: "%"
```

### 3. Plan → Apply

```bash
# See what would change
zbx plan configs/

# Apply when satisfied
zbx apply configs/
```

---

## Commands

### `zbx plan <path>`

Shows what would be created, modified or removed — no changes are made.

```
╭─ + template: linux-observability ──────────────────────────────╮
│   + item: CPU utilization  (system.cpu.util)                │
│   + item: Available memory  (vm.memory.size[available])     │
│   + trigger: High CPU utilization (>90% for 5 min)          │
│   + discovery_rule: Mounted filesystem discovery            │
╰────────────────────────────────────────────────────────────────╯

Plan: 3 to add, 0 to modify, 0 to remove
```

### `zbx apply <path>`

Applies the configuration. Shows the plan first, then prompts for confirmation.

```bash
zbx apply configs/                    # interactive confirmation
zbx apply configs/ --auto-approve     # skip confirmation (CI/CD)
zbx apply configs/ --dry-run          # same as plan
```

### `zbx diff <path>`

Compares local YAML state against the current Zabbix configuration.

```bash
zbx diff configs/
zbx diff configs/templates/nginx.yaml
```

### `zbx validate <path>`

Validates YAML files against the schema without connecting to Zabbix.

```bash
zbx validate configs/                # quiet mode
zbx validate configs/ --verbose      # list each template
```

### `zbx export <name>`

Exports an existing Zabbix template to YAML. Ideal for migrating existing templates to Git.

```bash
# Print to stdout
zbx export "Linux by Zabbix agent"

# Save to file
zbx export "Linux by Zabbix agent" --output configs/templates/linux-zabbix-agent.yaml

# Partial name search works too
zbx export linux
```

---

## YAML Schema Reference

### Template

| Field | Type | Required | Description |
|---|---|---|---|
| `template` | string | ✅ | Technical name (used as Zabbix host name) |
| `name` | string | — | Display name (defaults to `template`) |
| `description` | string | — | Human-readable description |
| `groups` | list[string] | — | Host groups (default: `["Templates"]`) |
| `items` | list[Item] | — | Monitored items |
| `triggers` | list[Trigger] | — | Alert triggers |
| `discovery_rules` | list[DiscoveryRule] | — | LLD rules |

### Item

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | ✅ | Display name |
| `key` | string | ✅ | Zabbix item key |
| `interval` | string | `60s` | Collection interval (e.g. `30s`, `5m`, `1h`) |
| `type` | enum | `zabbix_agent` | Item type |
| `value_type` | enum | `float` | Data type |
| `units` | string | `""` | Unit label (e.g. `%`, `B`, `bps`) |
| `description` | string | `""` | Item description |
| `history` | string | `90d` | History storage period |
| `trends` | string | `365d` | Trends storage period |
| `enabled` | bool | `true` | Whether the item is active |

**Item types:** `zabbix_agent`, `zabbix_agent_active`, `zabbix_trapper`, `simple_check`, `calculated`, `http_agent`, `snmp_v2c`

**Value types:** `float`, `unsigned`, `char`, `text`, `log`

### Trigger

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | ✅ | Trigger name |
| `expression` | string | ✅ | Zabbix trigger expression |
| `severity` | enum | `average` | Alert severity |
| `description` | string | `""` | Description / runbook notes |
| `enabled` | bool | `true` | Whether the trigger is active |

**Severities:** `not_classified`, `information`, `warning`, `average`, `high`, `disaster`

### DiscoveryRule

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | ✅ | Rule name |
| `key` | string | ✅ | LLD key |
| `interval` | string | `1h` | Discovery interval |
| `type` | enum | `zabbix_agent` | Same values as Item type |
| `item_prototypes` | list[ItemPrototype] | `[]` | Item prototypes |

---

## Project Structure

```
zbx/
├── zbx/
│   ├── cli.py              # Typer app + command registration
│   ├── models.py           # Pydantic models (Template, Item, Trigger …)
│   ├── config_loader.py    # YAML loading and schema validation
│   ├── zabbix_client.py    # Zabbix JSON-RPC HTTP client
│   ├── diff_engine.py      # Desired vs current state comparison
│   ├── deployer.py         # Apply logic (create / update)
│   ├── formatter.py        # Rich CLI output
│   └── commands/
│       ├── apply.py
│       ├── plan.py
│       ├── diff.py
│       ├── validate.py
│       └── export.py
├── configs/
│   └── templates/
│       ├── linux-observability.yaml
│       └── nginx.yaml
├── pyproject.toml
└── .env.example
```

---

## Git Workflow

```bash
# Create a feature branch
git checkout -b monitoring/add-redis-template

# Write your template
vim configs/templates/redis.yaml

# Validate locally
zbx validate configs/templates/redis.yaml

# See the plan against staging
ZBX_URL=http://zabbix-staging zbx plan configs/templates/redis.yaml

# Commit and open a PR
git add configs/templates/redis.yaml
git commit -m "feat: add Redis monitoring template"
git push origin monitoring/add-redis-template

# After PR approval, apply to production
zbx apply configs/templates/redis.yaml --auto-approve
```

---

## CI/CD Integration

### GitHub Actions

```yaml
# .github/workflows/zbx-apply.yml
name: Deploy monitoring config

on:
  push:
    branches: [main]
    paths: [configs/**]

jobs:
  apply:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e .
      - run: zbx validate configs/
      - run: zbx apply configs/ --auto-approve
        env:
          ZBX_URL: ${{ secrets.ZBX_URL }}
          ZBX_USER: ${{ secrets.ZBX_USER }}
          ZBX_PASSWORD: ${{ secrets.ZBX_PASSWORD }}
```

---

## Safety Behaviour

- **zbx never deletes** items, triggers or discovery rules that exist in Zabbix but are absent from config. It logs a warning instead. Removal must be done manually (intentional safety net).
- `zbx apply` always shows the plan and asks for confirmation unless `--auto-approve` is passed.
- All write operations are idempotent — running `zbx apply` twice produces the same result.

---

## Extending zbx

The architecture is designed for extension:

| What to add | Where |
|---|---|
| New resource type (host groups, macros) | Add model to `models.py`, CRUD to `zabbix_client.py`, diff logic to `diff_engine.py`, apply logic to `deployer.py` |
| New command | Add `zbx/commands/<cmd>.py`, register in `cli.py` |
| Custom output format (JSON, table) | Add formatter function to `formatter.py` |

---

## License

MIT
