#!/usr/bin/env bash
# demo.sh — zbxctl terminal demo
#
# Record with asciinema:
#   asciinema rec demo.cast --command "bash docs/demo.sh" --cols 100 --rows 28
#
# Convert to GIF:
#   pip install agg
#   agg demo.cast docs/demo.gif

set -euo pipefail

: "${ZABBIX_URL:=http://localhost:8080/zabbix}"
: "${ZABBIX_USER:=Admin}"
: "${ZABBIX_PASSWORD:=zabbix}"

say()  { echo; printf "\e[1;36m%s\e[0m\n" "$*"; sleep 0.6; }
cmd()  { printf "\e[1;32m\$ %s\e[0m\n" "$*"; sleep 0.4; eval "$*"; sleep 1; }
pause(){ sleep 1.2; }

clear
say "── zbxctl: Zabbix configuration as code ───────────────────────────────"
pause

say "1. Version + connectivity check"
cmd "zbx --version"
cmd "zbx status"
pause

say "2. Validate config before deploying"
cmd "zbx validate configs/checks/postgresql/"
pause

say "3. Plan — what would change? (no writes)"
cmd "zbx plan configs/checks/postgresql/"
pause

say "4. Apply — deploy the template"
cmd "zbx apply configs/checks/postgresql/"
pause

say "5. Apply is idempotent — running again makes no changes"
cmd "zbx apply configs/checks/postgresql/"
pause

say "6. Diff — compare YAML to live Zabbix"
cmd "zbx diff configs/checks/postgresql/"
pause

say "7. Export an existing template to YAML"
cmd "zbx export template 'Linux by Zabbix agent'"
pause

say "8. Saved plans for CI/CD gating"
cmd "zbx plan configs/checks/ --output /tmp/zbx.plan.json"
cmd "zbx apply --from-plan /tmp/zbx.plan.json --dry-run"
pause

say "── pip install zbxctl  |  github.com/psantana5/zbx ─────────────────────"
