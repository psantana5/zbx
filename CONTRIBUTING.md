# Contributing to zbxctl

Thank you for considering a contribution! zbxctl follows the same Git-first philosophy it applies to Zabbix — every change is reviewed, tested, and documented.

---

## Ways to contribute

| Type | Where to start |
|---|---|
| 🐛 Bug report | [Open a bug issue](../../issues/new?template=bug_report.yml) |
| 💡 Feature idea | [Open a feature request](../../issues/new?template=feature_request.yml) |
| 📦 New bundled check | [Open a check request](../../issues/new?template=new_check.yml) |
| 🔧 Code fix / improvement | Fork → branch → PR |
| 📝 Documentation | Edit any `.md` file and open a PR |

---

## Development setup

```bash
# 1. Fork and clone
git clone https://github.com/<your-user>/zbx.git
cd zbx

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install in editable mode with dev extras
pip install -e ".[dev]"

# 4. Verify tests pass
pytest tests/ -q
```

---

## Running against a local Zabbix

The repo includes a `docker-compose.yml` that spins up Zabbix + PostgreSQL:

```bash
docker compose up -d
# Wait ~60s for Zabbix to initialise, then:
zbx validate configs/checks/postgresql/
zbx plan     configs/checks/postgresql/
```

Default credentials: `Admin` / `zabbix` at `http://localhost:8080`.

---

## Branch naming

| Type | Pattern |
|---|---|
| Bug fix | `fix/<short-description>` |
| Feature | `feat/<short-description>` |
| Docs | `docs/<short-description>` |
| New check | `check/<check-name>` |

---

## Commit style

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(commands): add zbx template list command
fix(deployer): handle empty trigger list gracefully
docs(readme): add docker-compose quickstart
test(diff_engine): cover item-key collision case
```

---

## Pull request checklist

- [ ] Tests pass: `pytest tests/ -q`
- [ ] New behaviour has a test
- [ ] Public functions have docstrings
- [ ] `zbx validate configs/` passes for any new check YAML
- [ ] CHANGELOG.md updated (under `[Unreleased]`)

---

## Adding a bundled check

1. Create `configs/checks/<name>/check.yaml` — see existing checks for structure
2. If the check needs an external script, add it to `scripts/check_<name>.py` (stdlib only — no extra deps)
3. Run `zbx validate configs/checks/<name>/` to confirm the schema
4. Open a PR with the label `new-check`

---

## Code style

- **Black** for formatting (`black zbx/ tests/`)
- **isort** for imports (`isort zbx/ tests/`)
- **ruff** for linting (`ruff check zbx/`)

These run automatically in CI. You can run them locally before pushing.

---

## Questions?

Open a [Discussion](../../discussions) or drop a comment in any relevant issue.
