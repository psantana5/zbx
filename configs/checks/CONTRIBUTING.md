# Contributing a Check

A **check** is a self-contained monitoring unit: one folder that contains the
script(s) and the YAML file that defines the Zabbix template, items, triggers
and agent deployment config — all in one place.

## How to add a new check

### 1. Generate the skeleton

```bash
zbx scaffold my-check-name
```

This creates `configs/checks/my-check-name/` with:

```
configs/checks/my-check-name/
  check.yaml        ← everything Zabbix needs + agent deployment info
  my_check.py       ← placeholder script (replace with yours)
  README.md         ← describe what your check monitors
```

### 2. Write your script

Put your monitoring script in the check folder.  The script should either:

- **Print plain text** (a number, a string) for simple items.
- **Print a JSON object** for dependent items that split one API call into
  multiple metrics.
- **Print LLD-format JSON** (`{"data": [{"{#MACRO}": "value"}]}`) for
  discovery rules.

The Zabbix agent will call your script as the `zabbix` user, so make sure it
runs without requiring special session environment.

Test your script manually first:

```bash
sudo -u zabbix python3 configs/checks/my-check-name/my_script.py
```

### 3. Fill in `check.yaml`

`check.yaml` is a regular zbx template file **plus** an optional `agent:`
block that describes how the script is deployed.  The two sections are
independent — you can use one or both.

```yaml
# ── Template definition ────────────────────────────────────────────────────
template: my-check          # must be unique in Zabbix
name: "My Check"
description: "What this check monitors"
groups:
  - Templates
  - Templates/MyCategory

items:
  - name: My metric
    key: my.check.value
    interval: 60s
    value_type: float
    units: "%"

triggers:
  - name: My metric is too high
    expression: last(/my-check/my.check.value) > 90
    severity: high

# ── Agent deployment ───────────────────────────────────────────────────────
# Describes where the script lives and what UserParameters it registers.
# Used by: zbx agent deploy <host> --from-check configs/checks/my-check-name/
agent:
  scripts:
    - source: configs/checks/my-check-name/my_script.py
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

### 4. Validate

```bash
zbx validate configs/checks/my-check-name/
```

### 5. Preview what will be created in Zabbix

```bash
zbx plan configs/checks/my-check-name/
```

### 6. Deploy the template to Zabbix

```bash
zbx apply configs/checks/my-check-name/
```

### 7. Deploy the script to a host

```bash
zbx agent deploy <hostname> --from-check configs/checks/my-check-name/
```

> The host must exist in `inventory.yaml` with an `agent:` block that defines
> SSH / sudo credentials.  The check's scripts and userparameters are merged
> in automatically.

### 8. Verify it works

```bash
zbx agent test <hostname> --from-check configs/checks/my-check-name/
```

### 9. Open a PR

Submit a PR with your `configs/checks/my-check-name/` folder.  No other
files need to change.

---

## Check folder layout reference

```
configs/checks/<name>/
  check.yaml      required — template definition + optional agent block
  *.py / *.sh     your script(s)
  README.md       recommended — explains what the check does and prerequisites
```

## Rules

- The `template:` value in `check.yaml` must be globally unique in Zabbix.
- Script `source:` paths must be relative to the **repo root**.
- Keep scripts focused: one script per logical monitoring domain.
- Do not commit secrets — use Zabbix host macros (`{$MY_SECRET}`) for
  passwords and tokens.
