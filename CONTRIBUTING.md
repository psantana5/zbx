# Contributing to zbx

Thank you for contributing to **zbx** — Zabbix configuration as code.

This document covers everything you need to know, whether you are:

- **adding a new monitoring check** (the most common contribution),
- **fixing a bug or improving the CLI**,
- **using the AI maintainer** to automate issue processing, or
- **maintaining the project** long-term.

---

## Table of contents

1. [Project overview](#1-project-overview)
2. [Getting started](#2-getting-started)
3. [Repository structure](#3-repository-structure)
4. [Adding a monitoring check](#4-adding-a-monitoring-check)
5. [YAML schema reference](#5-yaml-schema-reference)
6. [Modifying the CLI or core logic](#6-modifying-the-cli-or-core-logic)
7. [Testing your changes](#7-testing-your-changes)
8. [Git workflow](#8-git-workflow)
9. [AI maintainer](#9-ai-maintainer)
10. [Code style](#10-code-style)

---

## 1. Project overview

zbx is a Python CLI tool that lets engineers manage Zabbix monitoring
configuration through YAML files and Git — the same mental model as
Terraform (`plan → apply → diff`).

```
zbx plan      configs/      See what would change
zbx apply     configs/      Apply changes to Zabbix
zbx diff      configs/      Compare local YAML against Zabbix
zbx validate  configs/      Validate YAML schema (no Zabbix connection)
zbx export    linux         Export an existing Zabbix template to YAML
zbx scaffold  my-check      Bootstrap a new check folder
```

The goal: every monitoring change goes through a pull request, is reviewed,
and is traceable in Git history.

---

## 2. Getting started

**Requirements:** Python 3.11+, pip

```bash
git clone https://github.com/psantana5/zbx
cd zbx
pip install -e .

zbx --version
```

Copy `.env.example` → `.env` and fill in your Zabbix connection:

```bash
cp .env.example .env
# ZBX_URL=http://zabbix.example.com/zabbix
# ZBX_USER=Admin
# ZBX_PASSWORD=zabbix
```

Verify the connection:

```bash
zbx inventory list
```

---

## 3. Repository structure

```
zbx/                        Python package — CLI and core logic
  cli.py                      Typer app entry point; all commands registered here
  models.py                   Pydantic models for every Zabbix object
  config_loader.py            Loads and validates YAML → model objects
  zabbix_client.py            Zabbix JSON-RPC API client (version-aware auth)
  deployer.py                 Creates and updates Zabbix resources
  diff_engine.py              Computes desired vs current state diff
  formatter.py                Rich terminal output (plan / diff / apply results)
  agent_deployer.py           SSH / local script deployment to monitored hosts
  commands/                   One module per CLI sub-command
    apply.py, plan.py, diff.py, validate.py
    export.py, scaffold.py
    agent.py, inventory.py

configs/
  templates/                  Standalone Zabbix template YAML files
  checks/                     Self-contained monitoring checks (preferred for new work)
    CONTRIBUTING.md             Check-specific contributor guide
    s3-monitoring/              Reference example
      check.yaml
      README.md
  hosts/                      Host playbook YAML files (template linking + macros)

scripts/                      Monitoring scripts (Python / shell)
inventory.yaml                Host inventory (hosts, groups, agent config)

.github/
  workflows/
    ai-maintainer.yml         GitHub Actions workflow for automated issue processing
  scripts/
    ai_maintainer.py          Agentic Python script called by the workflow
```

---

## 4. Adding a monitoring check

This is the most common contribution. A **check** is a self-contained folder
under `configs/checks/` that bundles:

- a monitoring script,
- a `check.yaml` that defines the Zabbix template **and** the agent deployment config.

No other files need to change.

### Step 1 — scaffold the folder

```bash
zbx scaffold my-check-name
```

This creates:

```
configs/checks/my-check-name/
  check.yaml             skeleton template + agent block
  my_check_name.py       placeholder script (replace with yours)
  README.md              document what your check monitors
```

### Step 2 — write your script

Replace `my_check_name.py` with your monitoring logic.

The Zabbix agent calls your script as the `zabbix` user with no login
environment. Test it manually before proceeding:

```bash
sudo -u zabbix python3 configs/checks/my-check-name/my_check_name.py
```

**Script conventions:**

| Output type | Use case |
|-------------|----------|
| Single number or string | Simple item (`value_type: float` or `char`) |
| JSON object with named keys | Dependent items that split one API call into multiple metrics |
| `{"data": [{"{#MACRO}": "value"}]}` | Low-Level Discovery rule |

### Step 3 — fill in `check.yaml`

A `check.yaml` is a regular zbx template file with an optional `agent:` block.

```yaml
# ── Zabbix template ───────────────────────────────────────────────────────
template: my-check-name          # must be globally unique in Zabbix
name: "My Check Name"
description: "What this check monitors"
groups:
  - Templates
  - Templates/MyCategory

items:
  - name: My metric
    key: my.check.value          # must match the UserParameter key below
    interval: 60s
    value_type: float
    units: "%"
    description: "..."

triggers:
  - name: My metric is too high
    expression: last(/my-check-name/my.check.value) > 90
    severity: high

# ── Agent deployment ──────────────────────────────────────────────────────
# Used by: zbx agent deploy <host> --from-check configs/checks/my-check-name/
agent:
  scripts:
    - source: configs/checks/my-check-name/my_check_name.py
      dest: /usr/local/scripts/zabbix/my_check_name.py
      mode: "0755"
  userparameters:
    - name: my-check-name
      parameters:
        - key: my.check.value
          command: /usr/local/scripts/zabbix/my_check_name.py
  test_keys:
    - my.check.value
```

> **Never commit secrets.** Use Zabbix host macros (`{$MY_SECRET}`) for
> passwords, tokens and environment-specific values.

### Step 4 — validate and preview

```bash
zbx validate configs/checks/my-check-name/
zbx plan     configs/checks/my-check-name/
```

### Step 5 — deploy and test (optional, with a real Zabbix instance)

```bash
# Apply the template to Zabbix
zbx apply configs/checks/my-check-name/

# Deploy the script to a host
zbx agent deploy <hostname> --from-check configs/checks/my-check-name/

# Verify the key works on the agent
zbx agent test <hostname> --from-check configs/checks/my-check-name/
```

### Step 6 — open a pull request

Create a branch, commit your files, and open a PR.
Only `configs/checks/my-check-name/` needs to be in the commit.

```bash
git checkout -b check/my-check-name
git add configs/checks/my-check-name/
git commit -m "feat(check): add my-check-name monitoring"
git push origin check/my-check-name
# then open a PR on GitHub
```

---

## 5. YAML schema reference

### Template

```yaml
template: template-id          # string, unique in Zabbix, used in trigger expressions
name: "Display Name"           # optional; defaults to template
description: "..."
groups:
  - Templates                  # at least one group required

items:
  - name: Item display name
    key: item.key[optional,params]
    interval: 60s              # s = seconds, m = minutes, h = hours, d = days
    type: zabbix_agent         # zabbix_agent | zabbix_agent_active | calculated |
                               # dependent | http_agent | snmp_v2c | zabbix_trapper
    value_type: float          # float | char | log | unsigned | text
    units: "%"
    description: "..."
    params: ""                 # formula for 'calculated' items; leave empty otherwise
    history: 90d
    trends: 365d
    tags:
      - tag: component
        value: cpu

triggers:
  - name: Trigger display name
    expression: avg(/template-id/item.key,5m) > 90
    severity: high             # not_classified | information | warning | average | high | disaster
    description: "When this fires and what to do"
    enabled: true
    tags:
      - tag: scope
        value: performance

discovery_rules:
  - name: Discovery rule name
    key: discovery.key
    interval: 1h
    type: zabbix_agent
    description: "..."
    item_prototypes:
      - name: "Item for [{#MACRO}]"
        key: "item[{#MACRO}]"
        interval: 60s
        value_type: float
        # For dependent item prototypes:
        type: dependent
        master_item_key: "master.item[{#MACRO}]"
        preprocessing:
          - type: jsonpath
            params: "$.field"
    trigger_prototypes:
      - name: "Alert for [{#MACRO}]"
        expression: "last(/template-id/item[{#MACRO}]) > 90"
        severity: warning
        recovery_expression: "last(/template-id/item[{#MACRO}]) < 80"
        allow_manual_close: true
```

### Host playbook (`configs/hosts/<hostname>.yaml`)

```yaml
host: hostname-in-zabbix
templates:
  - template-id-1
  - template-id-2
macros:
  - macro: "{$MY_PASSWORD}"
    value: "secret"
    description: "Used by the my-check script"
```

### Inventory entry (`inventory.yaml`)

```yaml
hosts:
  - host: my-server
    name: "My Server"
    ip: 192.168.1.10
    port: 10050
    groups:
      - Linux servers
    agent:
      ssh_user: deploy
      sudo: true
      scripts:
        - source: scripts/my_script.py
          dest: /usr/local/scripts/zabbix/my_script.py
          mode: "0755"
      userparameters:
        - name: my-check
          parameters:
            - key: my.check.value
              command: /usr/local/scripts/zabbix/my_script.py
      test_keys:
        - my.check.value
```

---

## 6. Modifying the CLI or core logic

### Adding a new CLI command

1. Create `zbx/commands/my_command.py`.
2. Define the command function (decorated with `@app.command()` or registered in `cli.py`).
3. Register it in `zbx/cli.py`:

```python
from zbx.commands.my_command import my_cmd
app.command("my-cmd", help="What it does.")(my_cmd)
```

### Adding a new model field

1. Add the field to the relevant Pydantic model in `zbx/models.py`.
2. Update `zbx/deployer.py` if the field must be sent to the Zabbix API.
3. Update `zbx/diff_engine.py` if the field should be compared during `plan`/`diff`.
4. Update `zbx/commands/export.py` if the field should be included in YAML exports.

### Zabbix API conventions

- Authentication is version-aware — do not hardcode the auth mechanism.
  Use `ZabbixClient` which handles `< 5.4` vs `>= 5.4` vs `>= 6.4` differences automatically.
- Trigger expressions returned by `selectTriggers` are **unexpanded** (internal IDs).
  Always fetch triggers via `trigger.get` with `expandExpression: True`.
- `calculated` items require a `params` field (the formula string).
- Dependent items require `delay: "0"` and a resolved `master_itemid` (internal ID, not key).
  The deployer handles this via a two-pass creation strategy.

---

## 7. Testing your changes

### Validate YAML (no Zabbix connection needed)

```bash
zbx validate configs/
```

### Dry-run against a real Zabbix instance

```bash
zbx plan configs/
```

### Apply and verify idempotency

```bash
zbx apply configs/ --auto-approve
zbx plan  configs/           # should output: No changes. Infrastructure is up-to-date.
```

### Export round-trip (for template changes)

```bash
zbx export <template-name> --output /tmp/rt.yaml
zbx plan /tmp/rt.yaml        # should output: No changes.
```

### Python syntax check

```bash
python3 -m py_compile zbx/**/*.py
```

### Linting (if ruff is installed)

```bash
ruff check zbx/
```

---

## 8. Git workflow

- **Never push directly to `main`.**
- All changes go through a pull request.
- Branch naming conventions:

| Type | Branch name |
|------|-------------|
| New check | `check/<name>` |
| Bug fix | `fix/<short-description>` |
| Feature | `feat/<short-description>` |
| AI-generated | `ai/issue-<number>-<short-description>` |

- Commit message format: `type(scope): short description`
  - e.g. `feat(check): add nginx monitoring`, `fix(deployer): handle calculated items`
- Add the co-author trailer to all commits:
  ```
  Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
  ```

---

## 9. AI maintainer

The repository includes a GitHub Actions workflow that automatically
processes issues labeled **`ai-task`**.

### How it works

1. Add the `ai-task` label to any issue.
2. The workflow triggers and runs `.github/scripts/ai_maintainer.py`.
3. The script uses **GitHub Models API** (`gpt-4o`) to read the issue,
   explore the codebase, and implement the requested change.
4. It creates a branch (`ai/issue-<N>-<description>`), commits the changes,
   pushes, and opens a pull request.
5. A comment is posted on the issue with a link to the PR.

### Writing good `ai-task` issues

The AI works best when the issue is specific. Good examples:

> **Add a PostgreSQL monitoring check**
>
> Add a new check under `configs/checks/postgres/` that monitors:
> - Active connections: `pg_stat_activity` count
> - Replication lag (seconds)
> - Database size (bytes)
>
> The script should connect via `psql` and output values as plain numbers.
> Trigger when active connections > 200 (warning) or > 400 (high).

Avoid vague requests like "improve monitoring" — the AI will skip them
and post an explanation on the issue.

### Manual re-run

If you need to re-process an issue (e.g. the first run failed), go to:

**Actions → AI Maintainer → Run workflow** and enter the issue number.

### Reviewing AI PRs

AI-generated PRs follow the same review process as human PRs.
Always verify:

- YAML validates: `zbx validate <path>`
- Plan shows expected changes: `zbx plan <path>`
- Trigger expressions reference the correct template ID
- No hardcoded credentials

---

## 10. Code style

| Rule | Detail |
|------|--------|
| Python version | 3.11+ (use `|` union types, `match`, etc.) |
| Formatter | `ruff format` (line length 100) |
| Linter | `ruff check` — `E`, `F`, `I`, `UP` rules |
| Type hints | Required on all public functions |
| Models | Pydantic v2 — use `model_validate`, `Field(default_factory=…)` |
| CLI | Typer — use `Annotated[type, typer.Option(…)]` for options |
| Output | Rich only — no bare `print()` for user-facing messages |
| Logging | `logging.getLogger(__name__)` — no `print()` for debug output |
| Comments | Only where logic is non-obvious; no line-by-line narration |
| Secrets | Never in code or YAML — use env vars or Zabbix host macros |

---

## Questions?

Open an issue or start a discussion on GitHub.
