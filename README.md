# zbx — Zabbix Configuration as Code

Manage Zabbix templates, items, triggers and discovery rules through YAML files
and Git — the same mental model as Terraform or Ansible.

```
zbx plan      configs/          See what would change
zbx apply     configs/          Apply changes to Zabbix
zbx diff      configs/          Compare local config against Zabbix
zbx validate  configs/          Validate YAML schema (no Zabbix connection)
zbx export    linux             Export an existing template to YAML

zbx inventory list              List all hosts in Zabbix
zbx inventory apply inventory.yaml  Create or update hosts in Zabbix

zbx agent diff   <host>         Preview agent-side changes
zbx agent deploy <host>         Deploy scripts and UserParameters via SSH
zbx agent test   <host>         Verify keys with zabbix_agentd -t
```

---

## Why zbx?

| Pain | Solution |
|---|---|
| Zabbix templates live only in the web UI | Store everything as YAML in Git |
| No audit trail for monitoring changes | Every change is a commit |
| Hard to review or approve changes | PR-based workflow, same as application code |
| Drift between environments | `zbx diff` catches it |
| Manual script deployment to monitored hosts | `zbx agent deploy` handles it |
| No way to automate template rollout | CI/CD friendly CLI |

---

## Installation

Requirements: Python 3.11+

```bash
git clone https://github.com/psantana5/zbx
cd zbx
pip install -e .

zbx --version
```

---

## Configuration

Copy `.env.example` to `.env` and fill in the connection details:

```bash
cp .env.example .env
```

```
ZBX_URL=http://zabbix.example.com/zabbix
ZBX_USER=Admin
ZBX_PASSWORD=secret
ZBX_VERIFY_SSL=true
```

Or export the variables directly (useful in CI/CD):

```bash
export ZBX_URL=http://zabbix.example.com/zabbix
export ZBX_USER=Admin
export ZBX_PASSWORD=secret
```

---

## Two-Layer Deployment Model

zbx manages both sides of Zabbix monitoring:

```
Layer 1 — Zabbix server config (templates, items, triggers, discovery rules)
    configs/templates/      YAML templates
    configs/hosts/          Host playbooks (which templates to link, which macros to set)

    zbx plan / apply / diff / validate / export

Layer 2 — Monitored host agent config (scripts + UserParameters)
    scripts/                Agent scripts versioned in Git
    inventory.yaml          Host inventory with agent deployment config

    zbx inventory apply     Create/update hosts in Zabbix
    zbx agent deploy        SSH into host, deploy scripts, write UserParameters
    zbx agent test          Run zabbix_agentd -t to verify keys
```

---

## Quick Start

### 1. Define a template

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

### 2. Plan, then apply

```bash
zbx plan   configs/templates/linux-observability.yaml
zbx apply  configs/templates/linux-observability.yaml
```

### 3. Define a host and link the template

```yaml
# configs/hosts/webserver01.yaml

host: webserver01
templates:
  - linux-observability
macros:
  - macro: "{$CUSTOM_THRESHOLD}"
    value: "90"
```

```bash
zbx apply configs/hosts/webserver01.yaml
```

---

## Commands

### zbx plan

Shows what would be created, modified or removed. No changes are made.

```
+ template: linux-observability
  + item: CPU utilization  (system.cpu.util)
  + item: Available memory  (vm.memory.size[available])
  + trigger: High CPU utilization (>90% for 5 min)
  + discovery_rule: Mounted filesystem discovery

Plan: 4 to add, 0 to modify, 0 to remove
```

### zbx apply

Applies the configuration. Shows the plan first, then prompts for confirmation.

```bash
zbx apply configs/                    # interactive confirmation
zbx apply configs/ --auto-approve     # skip confirmation (CI/CD)
zbx apply configs/ --dry-run          # same as plan
```

### zbx diff

Compares local YAML state against the current Zabbix configuration.

```bash
zbx diff configs/
zbx diff configs/templates/nginx.yaml
```

### zbx validate

Validates YAML files against the schema without connecting to Zabbix.

```bash
zbx validate configs/
zbx validate configs/ --verbose
```

### zbx export

Exports an existing Zabbix template to YAML. Use this to migrate existing
templates to Git.

```bash
zbx export "Linux by Zabbix agent"
zbx export "Linux by Zabbix agent" --output configs/templates/linux-zabbix-agent.yaml
zbx export linux        # partial name search
```

---

## Inventory and Host Management

### inventory.yaml

The inventory defines all hosts that should exist in Zabbix and how to connect
to them. It also defines agent-side deployment config (scripts and UserParameters).

```yaml
# inventory.yaml

hosts:
  - host: webserver01
    name: "Web Server 01"
    ip: 192.168.1.101
    port: 10050
    groups:
      - Linux servers
      - Web servers
    status: enabled

    agent:
      ssh_user: deploy
      sudo: true
      scripts:
        - source: scripts/check_nginx.sh
          dest: /usr/local/scripts/zabbix/check_nginx.sh
          owner: zabbix
          group: zabbix
          mode: "0755"
      userparameters:
        - name: nginx
          path: /etc/zabbix/zabbix_agentd.d/userparameters_nginx.conf
          parameters:
            - key: nginx.active_connections
              command: /usr/local/scripts/zabbix/check_nginx.sh connections
            - key: nginx.requests_per_sec
              command: /usr/local/scripts/zabbix/check_nginx.sh rps
      restart_agent: false
      test_keys:
        - nginx.active_connections
```

**Localhost shortcut:** If `ip` is `127.0.0.1`, `localhost`, or `::1`, zbx
skips SSH entirely and runs all commands locally via subprocess. No SSH key
setup required. `zbx agent deploy` will prompt for your sudo password once
at startup (hidden input) and use it for all writes to `/etc/zabbix/`.

### zbx inventory

```bash
zbx inventory list                      # table of all hosts in Zabbix
zbx inventory apply inventory.yaml      # create or update hosts
zbx inventory apply inventory.yaml --dry-run
```

### zbx agent

The agent commands deploy scripts and UserParameters to monitored hosts.
Scripts are stored in `scripts/` in the repo and versioned in Git. zbx
computes a SHA-256 checksum before every deploy — only changed files are
transferred.

```bash
zbx agent diff   webserver01   # show what would change on the host
zbx agent deploy webserver01   # copy scripts, write userparameters
zbx agent deploy webserver01 --dry-run
zbx agent deploy webserver01 --auto-approve   # skip confirmation (CI/CD)
zbx agent test   webserver01   # run zabbix_agentd -t for each test_key
zbx agent test   webserver01 --key nginx.active_connections  # ad-hoc test
```

For **remote hosts**, zbx connects over SSH (Paramiko). The user running zbx
must have SSH key access to the host. Password auth is not supported — use
`ssh-copy-id` to set up key-based auth first.

For **localhost**, zbx uses subprocess. If `sudo: true` is set in the agent
config, `zbx agent deploy` prompts for your sudo password once before making
any writes.

---

## Full Deployment Workflow

```bash
# 1. Add monitoring scripts to the repo
cp /path/to/script.py scripts/
git add scripts/script.py

# 2. Define the template (server side)
vim configs/templates/my-template.yaml

# 3. Define the inventory entry with agent config (host side)
vim inventory.yaml

# 4. Define the host playbook (template linking + macros)
vim configs/hosts/myhost.yaml

# 5. Deploy — server side
zbx validate configs/
zbx plan  configs/
zbx apply configs/

# 6. Deploy — host agent side
zbx inventory apply inventory.yaml   # ensure host exists in Zabbix
zbx agent diff   myhost              # preview
zbx agent deploy myhost              # copy scripts + write userparameters
zbx agent test   myhost              # verify keys

# 7. Commit everything
git add configs/ scripts/ inventory.yaml
git commit -m "feat: add monitoring for myhost"
```

---

## YAML Schema Reference

### Template

| Field | Type | Required | Description |
|---|---|---|---|
| `template` | string | yes | Technical name (used as Zabbix host name) |
| `name` | string | no | Display name (defaults to `template`) |
| `description` | string | no | Human-readable description |
| `groups` | list[string] | no | Template groups (default: `["Templates"]`) |
| `items` | list[Item] | no | Monitored items |
| `triggers` | list[Trigger] | no | Alert triggers |
| `discovery_rules` | list[DiscoveryRule] | no | LLD rules |

### Item

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | required | Display name |
| `key` | string | required | Zabbix item key |
| `interval` | string | `60s` | Collection interval (`30s`, `5m`, `1h`) |
| `type` | enum | `zabbix_agent` | Item type |
| `value_type` | enum | `float` | Data type |
| `units` | string | `""` | Unit label (`%`, `B`, `bps`) |
| `history` | string | `90d` | History retention |
| `trends` | string | `365d` | Trends retention |
| `enabled` | bool | `true` | Whether the item is active |
| `params` | string | `""` | Formula for `calculated` items; JSONPath/regex for `http_agent` |

Item types: `zabbix_agent`, `zabbix_agent_active`, `zabbix_trapper`,
`simple_check`, `calculated`, `http_agent`, `snmp_v2c`, `dependent`

> **`calculated` items require `params`** — set it to the formula string,
> e.g. `params: "avg(/mytemplate/my.key,5m)"`. Omitting it causes
> `Invalid parameter "/1": the parameter "params" is missing` from the API.

Value types: `float`, `unsigned`, `char`, `text`, `log`

### ItemPrototype (inside DiscoveryRule)

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | required | Display name |
| `key` | string | required | Item key (may contain LLD macros) |
| `type` | enum | `zabbix_agent` | Item type |
| `value_type` | enum | `float` | Data type |
| `master_item_key` | string | no | Key of master item (for `dependent` type) |
| `preprocessing` | list[Preprocessing] | `[]` | Preprocessing steps |

Preprocessing types: `jsonpath`, `regex`, `multiplier`, `trim`,
`not_match_regex`, `check_not_supported`, `discard_unchanged`

### Trigger

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | required | Trigger name |
| `expression` | string | required | Zabbix trigger expression |
| `severity` | enum | `average` | Alert severity |
| `description` | string | `""` | Description or runbook notes |
| `enabled` | bool | `true` | Whether the trigger is active |

Severities: `not_classified`, `information`, `warning`, `average`, `high`, `disaster`

### DiscoveryRule

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | required | Rule name |
| `key` | string | required | LLD key |
| `interval` | string | `1h` | Discovery interval |
| `type` | enum | `zabbix_agent` | Same values as Item type |
| `item_prototypes` | list[ItemPrototype] | `[]` | Item prototypes |
| `trigger_prototypes` | list[TriggerPrototype] | `[]` | Trigger prototypes |

### Host (playbook)

```yaml
host: myhost               # must match the technical hostname in Zabbix
templates:
  - linux-observability    # templates to link
  - custom-template
macros:
  - macro: "{$THRESHOLD}"
    value: "90"
    description: "Alert threshold percentage"
```

### InventoryHost

```yaml
host: myhost
name: "My Host Display Name"
ip: 192.168.1.100
port: 10050
groups:
  - Linux servers
description: "Optional description"
status: enabled            # enabled or disabled
```

---

## Project Structure

```
zbx/
├── zbx/
│   ├── cli.py              Typer app + command registration
│   ├── models.py           Pydantic models (Template, Item, Trigger, DiscoveryRule,
│   │                       Host, InventoryHost, AgentConfig, ScriptDeploy, ...)
│   ├── config_loader.py    YAML loading and schema validation
│   ├── zabbix_client.py    Zabbix JSON-RPC HTTP client (version-aware auth)
│   ├── diff_engine.py      Desired vs current state comparison
│   ├── deployer.py         Apply logic for templates and hosts
│   ├── agent_deployer.py   SSH/local agent deployment (scripts + UserParameters)
│   ├── formatter.py        Rich CLI output
│   └── commands/
│       ├── apply.py        zbx apply
│       ├── plan.py         zbx plan
│       ├── diff.py         zbx diff
│       ├── validate.py     zbx validate
│       ├── export.py       zbx export
│       ├── inventory.py    zbx inventory list / apply
│       └── agent.py        zbx agent diff / deploy / test
├── configs/
│   ├── templates/          Template YAML files
│   │   ├── linux-observability.yaml
│   │   └── nginx.yaml
│   └── hosts/              Host playbook YAML files
│       └── zabbixtest3100.yaml
├── scripts/                Agent scripts deployed by zbx agent deploy
│   └── README.md
├── inventory.yaml          Host inventory (groups, IPs, agent config)
├── pyproject.toml
├── .env.example
└── README.md
```

---

## Zabbix API Compatibility

| Zabbix version | Notes |
|---|---|
| < 5.4 | `user` field in login |
| >= 5.4 | `username` field in login |
| >= 6.2 | Templates require `templategroup`, not `hostgroup` |
| >= 6.4 | Auth token sent as `Authorization: Bearer` header (not in payload) |

zbx detects the API version on first connect via `apiinfo.version` and
adjusts automatically.

---

## Git Workflow

```bash
git checkout -b monitoring/add-redis-template

vim configs/templates/redis.yaml

zbx validate configs/templates/redis.yaml

ZBX_URL=http://zabbix-staging zbx plan configs/templates/redis.yaml

git add configs/templates/redis.yaml
git commit -m "feat: add Redis monitoring template"
git push origin monitoring/add-redis-template

# After PR approval
zbx apply configs/templates/redis.yaml --auto-approve
```

---

## CI/CD Integration

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
        with:
          python-version: "3.11"
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

- zbx never deletes items, triggers or discovery rules that exist in Zabbix
  but are absent from config. It logs a warning instead. Removal must be done
  manually — this is an intentional safety net.
- `zbx apply` always shows the plan and asks for confirmation unless
  `--auto-approve` is passed.
- All write operations are idempotent — running `zbx apply` twice produces
  the same result.
- `zbx agent deploy` uses SHA-256 checksums to skip files that have not
  changed.

---

## Extending zbx

| What to add | Where |
|---|---|
| New resource type (host groups, macros) | Model in `models.py`, CRUD in `zabbix_client.py`, diff in `diff_engine.py`, apply in `deployer.py` |
| New CLI command | `zbx/commands/<cmd>.py`, registered in `cli.py` |
| Custom output format | `formatter.py` |
| New agent check step | `agent_deployer.py` |

---

## License

MIT

