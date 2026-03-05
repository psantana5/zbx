# Changelog

All notable changes to **zbxctl** are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.4.0] — 2026-03-05

### Added
- `zbx plan --output plan.json` — save plan to JSON file for later apply
- `zbx apply --from-plan plan.json` — apply a previously saved plan (CI/CD gating)
- `zbx host list` — Rich table with IP, groups, status; `--group` filter, `--templates` flag
- `zbx host create` — create host from CLI args (--ip, --group, --template, --port)
- `zbx host delete` — delete host with confirmation, warns if templates are linked
- `zbx macro list` — list all global macros; `--search` filter
- `zbx macro set` — create or update a global macro (upsert, format validated)
- `zbx macro delete` — delete a global macro with confirmation
- `zbx hostgroup list` — list all host groups; `--hosts` count flag
- `zbx hostgroup create` — create a host group
- `zbx hostgroup delete` — delete an empty host group (refuses if hosts are members)
- 4 new bundled checks (14 total):
  - `windows-agent` — built-in agent keys (CPU, memory, uptime, OS info)
  - `apache-httpd` — mod_status scraper (stdlib only)
  - `mongodb` — TCP ping + mongosh fallback (stdlib only)
  - `jvm-jolokia` — Jolokia REST API (stdlib only, heap/GC/threads/classes)
- `.github/workflows/tests.yml` — CI runs unit tests on Python 3.11 + 3.12 with coverage
- `pytest-cov` added to dev dependencies; `[tool.coverage.*]` config in pyproject.toml
- Shell completion: `zbx --install-completion bash|zsh|fish` (Typer built-in)

### Changed
- `zbx apply` path argument is now optional when `--from-plan` is used

---

## [0.3.0] — 2026-03-05

### Added
- `zbx status` — show connection info, Zabbix API version, template/host counts
- `zbx check list` — table of all bundled checks with item/trigger/agent summary
- `zbx check info <name>` — detailed view of a check's items, triggers, and agent config
- `zbx check install <name> <host>` — apply template + deploy agent in one command
- Multi-environment profiles: `zbx --profile staging plan configs/`
  - `zbx.profiles.yaml` with named environments (url, user, password, …)
  - `ZBX_PROFILE` env var support
  - `zbx.profiles.yaml.example` added to repo; `zbx.profiles.yaml` gitignored
- 5 new bundled community checks: **mysql**, **rabbitmq**, **haproxy**, **elasticsearch**, **kubernetes-node**
  — all with check.yaml templates, agent blocks, and UserParameter scripts
- Unit tests: `tests/test_models.py` (51 tests) and `tests/test_diff_engine.py` (15 tests)
  — 88 total tests (66 unit + 22 e2e), all passing
- `HostMacro.macro` field validator: enforces `{$…}` or `{#…}` format

### Fixed
- `zbx status` now reads API version from the already-authenticated session
  (`version_str` property on `ZabbixClient`) instead of calling `apiinfo.version`
  again (which fails with Zabbix 7.x when an auth header is present)
- `inventory apply` now applies host macros (create + update + idempotency)
- `list_hosts` now requests `selectMacros` so macro diffs are correctly computed
- Stale `scripts/getS3Storage.py` reference removed from `inventory.yaml`

 — 2026-03-05

### Added
- `zbx export --all` — bulk export of every template from Zabbix to YAML files
- `zbx schema` — print JSON Schema and Markdown reference for all supported YAML fields
- `zbx export` now captures `master_item_key` for dependent items and discovery rules
- `zbx export` now captures `params` (formula) for calculated items and item prototypes
- `zbx export` now captures `master_item_key` for dependent item prototypes
- Full end-to-end integration test suite (22 tests)
- PyPI package published as `zbx-tool`

### Fixed
- **B20** Dependent discovery rules failed to create without `master_itemid`
- **B21** Calculated item prototype formulas (`params`) were dropped on export/apply
- **B19** Tags silently dropped from items and triggers during export
- **B18** Item/trigger prototypes on existing LLD rules not synced on apply
- **B17** Agent diff showed missing local scripts as changes-to-apply
- **B16** Tags never compared in diff or pushed on update
- **B15** LLD filter conditions not preserved in export round-trip
- **B12** LLD filter not sent to Zabbix on discovery rule create/update
- **B13** Plan summary double-counted template field changes
- **B14** Bare integer intervals (e.g. `60`) failed validation

### Changed
- `pyproject.toml` package name changed to `zbx-tool` for PyPI
- Bumped development status classifier to Beta

---

## [0.1.0] — 2026-02-01

### Added
- `zbx plan` — dry-run diff against live Zabbix
- `zbx apply` — create or update templates, items, triggers, discovery rules
- `zbx diff` — show differences between YAML config and Zabbix state
- `zbx validate` — offline schema validation
- `zbx export <name>` — export single template from Zabbix to YAML
- `zbx scaffold <name>` — bootstrap a new monitoring check folder
- `zbx inventory apply` — create/update hosts with template links and macros
- `zbx inventory list` — list all hosts in Zabbix
- `zbx agent deploy/diff/test` — SSH-based agent script deployment
- AI maintainer workflow (GitHub Actions + Copilot)
- Pydantic v2 models for all Zabbix objects
- LLD filter support (evaltype, conditions, formula)
- Dependent item and discovery rule support
- Tag support on items and triggers
