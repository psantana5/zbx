# Managing Zabbix like Terraform with zbxctl

*Published on Dev.to | Cross-post to Medium, Zabbix forum*

---

If you've used Terraform, Ansible, or ArgoCD, you already know the mental model:

```
plan → review → apply
```

Your infrastructure lives in Git. Changes are reviewed like code. Drift is visible. Rollback is a `git revert`.

**Why can't Zabbix monitoring work the same way?**

It can now — with **zbxctl**.

---

## The problem with the Zabbix UI

If you manage more than a handful of hosts, you've felt the pain:

- Templates modified directly in the web UI with no audit trail
- "Who changed that trigger expression?" answered with a shrug
- Copying templates between environments: export XML → import XML → pray
- Onboarding a new engineer means giving them UI access and hoping for the best

The configuration lives in the database, not in your codebase. It can't be reviewed, versioned, or tested.

---

## What zbxctl does

zbxctl brings the Terraform mental model to Zabbix:

```bash
# See what would change — no writes
zbx plan configs/templates/

# Apply the changes
zbx apply configs/templates/

# See drift between YAML and live Zabbix
zbx diff configs/templates/

# Export an existing template to YAML (migration path)
zbx export template "Linux by Zabbix agent"
```

Your Zabbix configuration lives in YAML files in Git. Engineers review changes as PRs. CI validates before deploy. The Zabbix UI becomes read-only.

---

## Quick start

```bash
pip install zbxctl

# Set your connection details
export ZABBIX_URL=https://zabbix.example.com
export ZABBIX_USER=Admin
export ZABBIX_PASSWORD=yourpassword

# Or use a .env file
cat > .env << EOF
ZABBIX_URL=https://zabbix.example.com
ZABBIX_USER=Admin
ZABBIX_PASSWORD=yourpassword
EOF

# Validate a config
zbx validate configs/checks/postgresql/

# Plan before applying
zbx plan configs/checks/postgresql/
```

---

## YAML template format

```yaml
template: postgresql-monitoring
description: "PostgreSQL performance and availability"

items:
  - name: "PostgreSQL: connections used"
    key: pg.connections.used
    interval: 60s
    type: external

  - name: "PostgreSQL: transactions per second"
    key: pg.tps
    interval: 30s

triggers:
  - name: "PostgreSQL: too many connections"
    expression: "last(/postgresql-monitoring/pg.connections.used) > 80"
    severity: warning

  - name: "PostgreSQL: service unavailable"
    expression: "max(/postgresql-monitoring/pg.connections.used,5m) = 0"
    severity: disaster
```

---

## 14 bundled checks

zbxctl ships with production-ready checks for:

| Check | What it monitors |
|---|---|
| postgresql | Connections, TPS, cache hit ratio, replication lag |
| redis | Memory, hit ratio, connected clients, replication |
| nginx | Active connections, request rate, error rate |
| mysql | Connections, queries, InnoDB metrics |
| elasticsearch | Cluster health, JVM heap, indexing rate |
| rabbitmq | Queue depth, message rates, node health |
| haproxy | Frontend/backend status, sessions, errors |
| mongodb | Connections, operations, replication lag |
| apache-httpd | Active workers, request rate (mod_status) |
| jvm-jolokia | Heap usage, GC time, thread count |
| windows-agent | CPU, memory, disk, network via built-in agent |
| linux-observability | The classic: CPU, memory, disk, network |

Install any of them in seconds:

```bash
zbx check install postgresql
zbx apply configs/checks/postgresql/
```

---

## CI/CD integration

Saved plans let you gate deployments:

```yaml
# .github/workflows/deploy.yml
- name: Plan
  run: zbx plan configs/ --output zbx.plan.json

- name: Apply (on main branch only)
  if: github.ref == 'refs/heads/main'
  run: zbx apply --from-plan zbx.plan.json
```

The plan file is an artifact. You can diff it in PRs, store it in S3, or require manual approval before applying.

---

## Try it locally

The repo includes a `docker-compose.yml`:

```bash
git clone https://github.com/psantana5/zbx.git
cd zbx
docker compose up -d
# Wait ~60s, then:
export ZABBIX_URL=http://localhost:8080/zabbix
export ZABBIX_USER=Admin
export ZABBIX_PASSWORD=zabbix
zbx status
zbx plan configs/checks/postgresql/
```

---

## The bigger picture

zbxctl is opinionated: Zabbix configuration belongs in Git. Once you adopt this workflow:

- **Onboarding** is `git clone` + `zbx apply`
- **Change review** is a pull request
- **Rollback** is `git revert` + `zbx apply`
- **Environment parity** is copying a folder

The Zabbix API has always supported this. zbxctl just makes it ergonomic.

---

**GitHub:** https://github.com/psantana5/zbx  
**PyPI:** `pip install zbxctl`  
**Zabbix versions:** 6.x, 7.x

*Stars, issues and PRs very welcome.*
