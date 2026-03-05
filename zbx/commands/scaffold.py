"""zbx scaffold — bootstrap a new check folder.

Usage:
    zbx scaffold <name>

Creates configs/checks/<name>/ with a skeleton check.yaml, a placeholder
script and a README — ready to fill in and submit as a PR.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

console = Console()

_CHECK_YAML_TEMPLATE = """\
# {name} check
#
# Deploy with:
#   zbx apply configs/checks/{name}/
#
# Deploy agent script to a host:
#   zbx agent deploy <hostname> --from-check configs/checks/{name}/

# ── Template definition ───────────────────────────────────────────────────

template: {name}
name: "{display_name}"
description: "TODO: describe what this check monitors"
groups:
  - Templates
  - Templates/Custom   # change to a category that fits

items:
  - name: TODO metric name
    key: {name}.value          # must match the UserParameter key below
    interval: 60s
    value_type: float
    units: "%"
    description: "TODO: describe what this item measures"

triggers:
  - name: TODO metric is too high
    expression: last(/{name}/{name}.value) > 90
    severity: warning
    description: "TODO: describe when this trigger fires"

# Uncomment and fill in if you need Low-Level Discovery:
# discovery_rules:
#   - name: {display_name} discovery
#     key: {name}.discover
#     interval: 1h
#     item_prototypes:
#       - name: "Item for [{{#MACRO}}]"
#         key: "{name}.item[{{#MACRO}}]"
#         interval: 60s
#         value_type: float

# ── Agent deployment ──────────────────────────────────────────────────────
# Used by: zbx agent deploy <host> --from-check configs/checks/{name}/

agent:
  scripts:
    - source: configs/checks/{name}/{script_name}
      dest: /usr/local/scripts/zabbix/{script_name}
      owner: zabbix
      group: zabbix
      mode: "0755"

  userparameters:
    - name: {name}
      parameters:
        - key: {name}.value
          command: /usr/local/scripts/zabbix/{script_name}
        # Add more keys as needed:
        # - key: "{name}.status[*]"
        #   command: /usr/local/scripts/zabbix/{script_name} $1

  test_keys:
    - {name}.value
"""

_SCRIPT_TEMPLATE = """\
#!/usr/bin/env python3
\"\"\"
{name} — Zabbix monitoring script.

Calling conventions:

    # Simple value (matches the {name}.value UserParameter)
    {script_name}           → prints a single number or string

    # With arguments (if you use key[*] parameters)
    {script_name} arg1      → prints a value for arg1

Exit codes:
    0  success
    1  error (Zabbix will record ZBX_NOTSUPPORTED)
\"\"\"

import sys


def main() -> None:
    # TODO: implement your monitoring logic here
    # args = sys.argv[1:]

    # Example: always return 0 (replace with real logic)
    print(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {{exc}}", file=sys.stderr)
        sys.exit(1)
"""

_README_TEMPLATE = """\
# {display_name} Check

TODO: one-sentence description of what this check monitors.

## How it works

TODO: explain the monitoring approach.

## Prerequisites

| Requirement | Details |
|-------------|---------|
| TODO        | TODO    |

## Deployment

```bash
# Apply the Zabbix template
zbx apply configs/checks/{name}/

# Deploy the agent script to a host
zbx agent deploy <hostname> --from-check configs/checks/{name}/

# Verify it works
zbx agent test <hostname> --from-check configs/checks/{name}/
```

## Script: {script_name}

TODO: describe what the script does and any arguments it accepts.
"""


def scaffold_cmd(
    name: str = typer.Argument(
        ...,
        help="Check name (used as folder name and Zabbix template ID, e.g. 'nginx-check').",
        metavar="NAME",
    ),
    checks_dir: Path = typer.Option(
        Path("configs/checks"),
        "--dir",
        "-d",
        help="Parent directory for checks.",
    ),
) -> None:
    """Bootstrap a new check folder with skeleton files.

    Creates configs/checks/NAME/ with:
    \\b
      check.yaml        template + agent deployment definition
      NAME_check.py     placeholder monitoring script
      README.md         contributor documentation
    """
    target = checks_dir / name
    if target.exists():
        console.print(f"[red]fail[/red] Directory already exists: {target}")
        raise typer.Exit(1)

    target.mkdir(parents=True)

    display_name = name.replace("-", " ").replace("_", " ").title()
    script_name = f"{name.replace('-', '_')}.py"

    (target / "check.yaml").write_text(
        _CHECK_YAML_TEMPLATE.format(
            name=name, display_name=display_name, script_name=script_name
        )
    )
    script_path = target / script_name
    script_path.write_text(
        _SCRIPT_TEMPLATE.format(name=name, script_name=script_name)
    )
    script_path.chmod(0o755)

    (target / "README.md").write_text(
        _README_TEMPLATE.format(name=name, display_name=display_name, script_name=script_name)
    )

    console.print(f"[green]ok[/green] Scaffolded check: [bold]{target}[/bold]")
    console.print()
    console.print("  [dim]Files created:[/dim]")
    for f in sorted(target.iterdir()):
        console.print(f"    {f.relative_to(checks_dir.parent)}")
    console.print()
    console.print("[dim]Next steps:[/dim]")
    console.print(f"  1. Edit [bold]{target}/check.yaml[/bold] — fill in items, triggers, agent block")
    console.print(f"  2. Replace [bold]{target}/{script_name}[/bold] with your monitoring script")
    console.print(f"  3. [bold]zbx validate configs/checks/{name}/[/bold]")
    console.print(f"  4. [bold]zbx plan     configs/checks/{name}/[/bold]")
    console.print(f"  5. [bold]zbx apply    configs/checks/{name}/[/bold]")
    console.print(f"  6. [bold]zbx agent deploy <host> --from-check configs/checks/{name}/[/bold]")
    console.print()
    console.print(
        f"  See [link=configs/checks/CONTRIBUTING.md]configs/checks/CONTRIBUTING.md[/link] for the full guide."
    )
