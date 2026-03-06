# zbxctl — Zabbix Configuration as Code

[![PyPI](https://img.shields.io/pypi/v/zbxctl?color=blue)](https://pypi.org/project/zbxctl/)
[![Python](https://img.shields.io/pypi/pyversions/zbxctl)](https://pypi.org/project/zbxctl/)
[![Tests](https://github.com/psantana5/zbx/actions/workflows/tests.yml/badge.svg)](https://github.com/psantana5/zbx/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zabbix](https://img.shields.io/badge/Zabbix-6.x%20%7C%207.x-red)](https://www.zabbix.com)

> **Manage Zabbix templates, items, triggers and discovery rules through YAML files and Git — the same mental model as Terraform, Ansible or ArgoCD.**

```bash
pip install zbxctl
```

<!-- demo GIF — record with: asciinema rec demo.cast --command "bash docs/demo.sh" -->
<!-- convert to GIF: pip install agg && agg demo.cast docs/demo.gif -->
![zbxctl demo](docs/demo.gif)

---

```
zbx plan      configs/          See what would change
zbx plan      configs/ --output plan.json   Save plan to file
zbx apply     configs/          Apply changes to Zabbix
zbx apply     --from-plan plan.json         Apply a saved plan (CI/CD gating)
zbx diff      configs/          Compare local config against Zabbix
zbx validate  configs/          Validate YAML schema (no Zabbix connection)
zbx export    linux             Export an existing template to YAML
zbx export    --all             Export every template to configs/templates/
zbx schema                      Print YAML field reference (Markdown or JSON Schema)
zbx scaffold  my-check          Bootstrap a new monitoring check folder
zbx status                      Show connection status and server summary

zbx host list                   List all hosts in Zabbix
zbx host create <host> --ip ... Create a host from the CLI
zbx host delete <host>          Delete a host

zbx hostgroup list              List all host groups
zbx hostgroup create <name>     Create a host group
zbx hostgroup delete <name>     Delete an empty host group

zbx macro list                  List all global macros
zbx macro set {$NAME} value     Create or update a global macro
zbx macro delete {$NAME}        Delete a global macro

zbx inventory list              List all hosts in Zabbix
zbx inventory apply inventory.yaml  Create or update hosts in Zabbix

zbx check list                  List all bundled monitoring checks
zbx check info  <name>          Show items, triggers and agent details for a check
zbx check install <name> <host> Apply template + deploy agent in one step

zbx agent diff   <host>         Preview agent-side changes
zbx agent deploy <host>         Deploy scripts and UserParameters via SSH
zbx agent test   <host>         Verify keys with zabbix_agentd -t

zbx agent deploy <host> --from-check configs/checks/my-check/
zbx agent test   <host> --from-check configs/checks/my-check/

zbx --profile staging plan configs/   Use a named environment profile
zbx --install-completion bash          Enable shell completion (bash/zsh/fish)
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

**From PyPI (recommended):**

```bash
pip install zbxctl
zbx --version
```

**From source (for development):**

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

### Multi-environment profiles

For teams with multiple environments (staging, production, dev), create a
`zbx.profiles.yaml` (gitignored — contains credentials):

```yaml
# zbx.profiles.yaml
staging:
  ZBX_URL: http://zabbix-staging.example.com/zabbix
  ZBX_USER: Admin
  ZBX_PASSWORD: staging-secret

production:
  ZBX_URL: https://zabbix.example.com/zabbix
  ZBX_USER: Admin
  ZBX_PASSWORD: prod-secret
  ZBX_VERIFY_SSL: "true"
```

Then switch environments with:

```bash
zbx --profile staging plan configs/
zbx --profile production apply configs/ --auto-approve

# Or set via environment variable
export ZBX_PROFILE=staging
zbx plan configs/
```

Copy `zbx.profiles.yaml.example` from the repo as a starting point.

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

Save the plan to a file for later use (Terraform-style):

```bash
zbx plan configs/ --output plan.json
# Plan saved to plan.json. Run zbx apply --from-plan plan.json to apply it.
```

### zbx apply

Applies the configuration. Shows the plan first, then prompts for confirmation.

```bash
zbx apply configs/                    # interactive confirmation
zbx apply configs/ --auto-approve     # skip confirmation (CI/CD)
zbx apply configs/ --dry-run          # same as plan
zbx apply --from-plan plan.json       # apply a previously saved plan
```

**CI/CD gating pattern (Terraform-style):**

```bash
# In your review/plan stage:
zbx plan configs/ --output zbx.plan

# After approval, in your apply stage:
zbx apply --from-plan zbx.plan --auto-approve
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

# Export every template at once
zbx export --all                              # writes to configs/templates/
zbx export --all --out-dir my-backup/
```

### zbx schema

Prints the full YAML field reference. Useful when writing templates by hand.

```bash
zbx schema                          # Markdown table (default)
zbx schema --format json            # JSON Schema (for editor integration)
zbx schema --output docs/schema.md  # write to file
```

### zbx scaffold

Bootstraps a new self-contained monitoring check folder under `configs/checks/`.
The generated skeleton includes a `check.yaml`, a placeholder script and a `README.md`.

```bash
zbx scaffold my-check-name
# creates configs/checks/my-check-name/{check.yaml, my_check_name.py, README.md}
```

After scaffolding, edit the generated files and follow the contributor workflow
described in [CONTRIBUTING.md](CONTRIBUTING.md).

### zbx status

Shows connection info and a summary of the Zabbix server state.

```bash
zbx status
```

```
zbx version   : 0.4.0
Zabbix URL    : http://zabbix.example.com/zabbix
API version   : 7.4.7
Auth user     : Admin
Templates     : 210
Hosts         : 42
```

### zbx check

Browse and deploy bundled monitoring checks.

```bash
zbx check list                        # table of all checks with item/trigger counts
zbx check info postgresql             # full details: items, triggers, agent deploy info
zbx check install postgresql myhost   # apply template + deploy agent in one command
```

`zbx check install` is a shortcut for:

```bash
zbx apply configs/checks/postgresql/
zbx agent deploy myhost --from-check configs/checks/postgresql/
```

### zbx host

Manage Zabbix hosts directly from the CLI without an `inventory.yaml`.

```bash
zbx host list                                    # table of all hosts
zbx host list --group "Linux servers"            # filter by group
zbx host list --templates                        # show linked templates column

zbx host create myserver \
  --ip 192.168.1.10 \
  --group "Linux servers" \
  --template "Linux by Zabbix agent" \
  --template postgresql                          # link multiple templates

zbx host delete myserver                         # with confirmation prompt
zbx host delete myserver --force                 # skip confirmation
```

### zbx hostgroup

Manage host groups.

```bash
zbx hostgroup list               # all groups
zbx hostgroup list --hosts       # show host count per group
zbx hostgroup list --search prod # filter by name

zbx hostgroup create "Production Linux"
zbx hostgroup delete "Old Group"          # refuses if group has hosts
```

### zbx macro

Manage global macros (applies to all hosts/templates in Zabbix).

```bash
zbx macro list                            # all global macros
zbx macro list --search SLACK             # filter by name

zbx macro set '{$SLACK_WEBHOOK}' 'https://hooks.slack.com/...'
zbx macro set '{$SNMP_COMMUNITY}' public --description "Default SNMP v2"

zbx macro delete '{$OLD_MACRO}'
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
zbx inventory apply inventory.yaml      # create or update hosts + macros
zbx inventory apply inventory.yaml --dry-run
```

Macro diff output example:
```
~ host: webserver01  (macros (+{$CPU_THRESHOLD}, ~{$MEM_THRESHOLD}))

  Inventory Summary
  1 to update
    + macro {$CPU_THRESHOLD} on 'webserver01'
    ~ macro {$MEM_THRESHOLD} on 'webserver01'
ok Inventory applied: 0 created, 1 updated.
```

### zbx agent

The agent commands deploy scripts and UserParameters to monitored hosts.
Scripts are stored in `scripts/` (or inside a check folder) in the repo and
versioned in Git. zbx computes a SHA-256 checksum before every deploy —
only changed files are transferred.

```bash
zbx agent diff   webserver01   # show what would change on the host
zbx agent deploy webserver01   # copy scripts, write userparameters
zbx agent deploy webserver01 --dry-run
zbx agent deploy webserver01 --auto-approve   # skip confirmation (CI/CD)
zbx agent test   webserver01   # run zabbix_agentd -t for each test_key
zbx agent test   webserver01 --key nginx.active_connections  # ad-hoc test
```

#### `--from-check` — deploy a self-contained check

If a check lives under `configs/checks/`, you can deploy its scripts and
UserParameters without touching `inventory.yaml`. zbx merges the check's
`agent:` block into the host's existing config automatically:

```bash
zbx agent diff   webserver01 --from-check configs/checks/nginx/
zbx agent deploy webserver01 --from-check configs/checks/nginx/
zbx agent test   webserver01 --from-check configs/checks/nginx/
```

For **remote hosts**, zbx connects over SSH (Paramiko). The user running zbx
must have SSH key access to the host. Password auth is not supported — use
`ssh-copy-id` to set up key-based auth first.

For **localhost**, zbx uses subprocess. If `sudo: true` is set in the agent
config, `zbx agent deploy` prompts for your sudo password once before making
any writes.

---

## Full Deployment Workflow

### Option A — traditional (script + separate template)

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

### Option B — self-contained check (recommended for new contributions)

```bash
# 1. Bootstrap the check folder
zbx scaffold my-check

# 2. Write the script and fill in check.yaml
vim configs/checks/my-check/my_check.py
vim configs/checks/my-check/check.yaml

# 3. Validate and preview
zbx validate configs/checks/my-check/
zbx plan     configs/checks/my-check/

# 4. Apply the template to Zabbix
zbx apply configs/checks/my-check/

# 5. Deploy script to host (no inventory.yaml changes needed)
zbx agent deploy myhost --from-check configs/checks/my-check/
zbx agent test   myhost --from-check configs/checks/my-check/

# 6. Commit
git add configs/checks/my-check/
git commit -m "feat(check): add my-check monitoring"
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
templates:
  - linux-observability    # pre-link templates at host creation time
macros:
  - macro: "{$CPU_THRESHOLD}"
    value: "90"
    description: "CPU alert threshold (%)"
  - macro: "{$MEM_THRESHOLD}"
    value: "80"
    description: "Memory alert threshold (%)"
```

Macros are applied idempotently: `zbx inventory apply` creates missing macros,
updates changed values, and skips unchanged ones. Running it twice produces
the same result.

---

## Bundled Community Checks

zbx ships with 14 ready-to-use monitoring checks under `configs/checks/`.
Each includes a Zabbix template **and** an `agent:` block so the script and
UserParameter can be deployed with a single command.

| Check | Folder | Keys |
|---|---|---|
| PostgreSQL | `configs/checks/postgresql/` | `postgresql.stat[ping]`, `postgresql.stat[connections.active]`, … |
| Redis | `configs/checks/redis/` | `redis.stat[ping]`, `redis.stat[connected_clients]`, … |
| Nginx | `configs/checks/nginx/` | `nginx.stat[ping]`, `nginx.stat[active]`, … |
| Docker | `configs/checks/docker/` | `docker.stat[ping]`, `docker.stat[containers.running]`, … |
| SSL cert | `configs/checks/ssl-cert/` | `ssl.cert[days_remaining,host:443]`, `ssl.cert[valid,host:443]` |
| MySQL | `configs/checks/mysql/` | `mysql.stat[ping]`, `mysql.stat[threads_connected]`, … |
| RabbitMQ | `configs/checks/rabbitmq/` | `rabbitmq.stat[ping]`, `rabbitmq.stat[messages_ready]`, … |
| HAProxy | `configs/checks/haproxy/` | `haproxy.stat[ping]`, `haproxy.stat[active_backends]`, … |
| Elasticsearch | `configs/checks/elasticsearch/` | `elasticsearch.stat[ping]`, `elasticsearch.stat[status]`, … |
| Kubernetes node | `configs/checks/kubernetes-node/` | `k8s.node[ping]`, `k8s.node[pods_running]`, … |
| Windows agent | `configs/checks/windows-agent/` | `system.cpu.util`, `vm.memory.size[available]`, `system.uptime`, … |
| Apache httpd | `configs/checks/apache-httpd/` | `apache.stat[ping]`, `apache.stat[busy_workers]`, … |
| MongoDB | `configs/checks/mongodb/` | `mongodb.stat[ping]`, `mongodb.stat[connections.active]`, … |
| JVM (Jolokia) | `configs/checks/jvm-jolokia/` | `jvm.jolokia[heap.usage_pct]`, `jvm.jolokia[threads.count]`, … |

Browse and install checks interactively:

```bash
zbx check list                        # see all checks
zbx check info mysql                  # items, triggers, agent details
zbx check install mysql db-server-01  # apply + deploy in one command
```

**Deploy a check manually (step-by-step):**

```bash
# 1. Apply the template to Zabbix
zbx apply configs/checks/postgresql/

# 2. Preview what the agent deploy would do
zbx agent diff myhost --from-check configs/checks/postgresql/

# 3. Deploy script + UserParameter to the monitored host
zbx agent deploy myhost --from-check configs/checks/postgresql/

# 4. Verify the keys are working
zbx agent test myhost --from-check configs/checks/postgresql/
```

Scripts are installed to `/usr/local/zbx/scripts/` and UserParameters are
written to `/etc/zabbix/zabbix_agentd.d/zbx-<check>.conf`.

---

## Project Structure

```
zbx/
├── zbx/
│   ├── cli.py              Typer app + command registration
│   ├── models.py           Pydantic models (Template, Item, Trigger, DiscoveryRule,
│   │                       Host, InventoryHost, AgentConfig, ScriptDeploy, ...)
│   ├── config_loader.py    YAML loading, schema validation, profile support
│   ├── zabbix_client.py    Zabbix JSON-RPC HTTP client (version-aware auth)
│   ├── diff_engine.py      Desired vs current state comparison
│   ├── deployer.py         Apply logic for templates and hosts
│   ├── agent_deployer.py   SSH/local agent deployment (scripts + UserParameters)
│   ├── plan_serializer.py  Serialize/deserialize plan diffs to/from JSON
│   ├── formatter.py        Rich CLI output
│   └── commands/
│       ├── apply.py        zbx apply (--from-plan support)
│       ├── plan.py         zbx plan (--output support)
│       ├── diff.py         zbx diff
│       ├── validate.py     zbx validate
│       ├── export.py       zbx export / zbx export --all
│       ├── scaffold.py     zbx scaffold
│       ├── schema.py       zbx schema (field reference / JSON Schema)
│       ├── inventory.py    zbx inventory list / apply (with macro support)
│       ├── agent.py        zbx agent diff / deploy / test
│       ├── status.py       zbx status (connection + server summary)
│       ├── check.py        zbx check list / info / install
│       ├── host.py         zbx host list / create / delete
│       ├── hostgroup.py    zbx hostgroup list / create / delete
│       └── macro.py        zbx macro list / set / delete
├── configs/
│   ├── templates/          Standalone template YAML files
│   │   ├── linux-observability.yaml
│   │   └── nginx.yaml
│   ├── checks/             Self-contained monitoring checks (14 bundled)
│   │   ├── CONTRIBUTING.md   How to add a new check
│   │   ├── postgresql/       PostgreSQL monitoring
│   │   ├── redis/            Redis monitoring
│   │   ├── nginx/            Nginx stub_status monitoring
│   │   ├── docker/           Docker daemon monitoring
│   │   ├── ssl-cert/         SSL certificate expiry
│   │   ├── mysql/            MySQL / MariaDB monitoring
│   │   ├── rabbitmq/         RabbitMQ management API monitoring
│   │   ├── haproxy/          HAProxy stats monitoring
│   │   ├── elasticsearch/    Elasticsearch REST API monitoring
│   │   ├── kubernetes-node/  Kubernetes node Kubelet monitoring
│   │   ├── windows-agent/    Windows built-in agent keys (no script)
│   │   ├── apache-httpd/     Apache mod_status monitoring
│   │   ├── mongodb/          MongoDB monitoring
│   │   ├── jvm-jolokia/      JVM monitoring via Jolokia REST API
│   │   ├── system-health/    CPU / memory / disk (built-in keys, no script)
│   │   └── s3-monitoring/    Reference example with custom script
│   └── hosts/              Host playbook YAML files
│       └── zabbixtest3100.yaml
├── scripts/                Agent scripts (legacy; prefer configs/checks/ for new work)
│   └── README.md
├── tests/
│   ├── test_models.py      Unit tests — Pydantic model validation (51 tests)
│   ├── test_diff_engine.py Unit tests — diff engine logic (15 tests)
│   └── test_e2e.py         End-to-end integration tests vs live Zabbix (22 tests)
├── inventory.yaml          Host inventory (groups, IPs, agent config)
├── zbx.profiles.yaml.example  Multi-environment profile template
├── .github/
│   ├── workflows/
│   │   ├── publish.yml         PyPI publish on vX.Y.Z tag (OIDC trusted publishing)
│   │   ├── tests.yml           Run test suite on push/PR
│   │   └── ai-maintainer.yml   Automated issue processing via Claude
│   └── scripts/
│       └── ai_maintainer.py    Agentic implementation
├── CONTRIBUTING.md
├── CHANGELOG.md
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

## CI/CD Integration — GitOps Workflow

The repository ships with `.github/workflows/zbx-gitops.yml`, a Terraform-style
GitOps pipeline:

| Event | Action |
|---|---|
| Pull request touches `configs/` | `zbx validate` + `zbx plan` — result posted as PR comment |
| Merge to `main` | `zbx apply --auto-approve` — changes deployed to Zabbix |

**Required secrets** (Settings → Secrets and variables → Actions):

```
ZBX_URL       https://zabbix.example.com
ZBX_USER      Admin
ZBX_PASSWORD  secret
```

**Manual trigger**: Go to Actions → zbx GitOps → Run workflow → choose `plan` or `apply`.

### Private Zabbix (localhost / VPN)

GitHub's cloud runners cannot reach a Zabbix server on a private network.
Use a [self-hosted runner](https://docs.github.com/en/actions/hosting-your-own-runners)
on a machine that has network access to your Zabbix:

```yaml
# In .github/workflows/zbx-gitops.yml, change:
runs-on: ubuntu-latest
# to:
runs-on: self-hosted
```

### PyPI auto-publish

Tag a release to publish `zbxctl` to PyPI automatically via `.github/workflows/publish.yml`:

```bash
git tag v1.0.0
git push origin v1.0.0
```

The workflow uses OIDC trusted publishing — no API token needed once configured
at [pypi.org/manage/project/zbxctl/settings/publishing/](https://pypi.org/manage/project/zbxctl/settings/publishing/).

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
| New monitoring check | `zbx scaffold <name>` → fill in `configs/checks/<name>/` |

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contributor guide.

---

## AI Maintainer

The repository includes a GitHub Actions workflow that automatically processes
issues labeled **`ai-task`** using **Claude** (via GitHub Models API).

### How it works

1. Open an issue describing the change, bug fix or new check needed.
2. Add the `ai-task` label.
3. The workflow triggers, runs an agentic loop:
   - reads the issue and explores the codebase,
   - writes the necessary files,
   - validates any YAML,
   - commits and pushes to a new branch,
   - opens a pull request.
4. A comment is posted on the issue with a link to the PR.

### Writing effective `ai-task` issues

Be specific. Good example:

> **Add a PostgreSQL monitoring check**
>
> Add `configs/checks/postgres/` that monitors:
> - Active connections (`pg_stat_activity` count) — trigger: warning > 200, high > 400
> - Replication lag in seconds
> - Database size in bytes
>
> Script should output plain numbers. Use `psql` to query.

### Manual re-run

**Actions → AI Maintainer → Run workflow** → enter the issue number.

---

## License

MIT

