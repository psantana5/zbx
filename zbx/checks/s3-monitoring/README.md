# S3 / MinIO User Storage Check

Monitors per-user storage quota, used space and available space on S3-compatible
object storage (AWS S3, MinIO, Ceph RGW, etc.).

## How it works

1. The Zabbix agent runs `getS3Storage.py` to discover S3 users (`s3.user.discover`).
2. For each discovered user the agent fetches raw JSON metrics (`s3.user.metrics[user,pass,group]`).
3. Three dependent items parse the JSON to extract `percentageused`, `usedspace` and `totalavailablesize`.
4. Triggers fire when any user's storage exceeds **95 %** of their quota.

## Prerequisites

| Requirement | Details |
|-------------|---------|
| Python 3 on host | `python3` must be available |
| S3 credentials | Passed via Zabbix host macros (see below) |
| Zabbix agent | `zabbix_agentd` running and reachable |

## Host macros

Set these on every host that links this template:

| Macro | Description |
|-------|-------------|
| `{$S3_USER_PASSWORD}` | S3 user password |
| `{$S3_USER_GROUP}` | S3 user group |

## Deployment

```bash
# 1. Apply the Zabbix template
zbx apply configs/checks/s3-monitoring/

# 2. Deploy the agent script to the host
zbx agent deploy <hostname> --from-check configs/checks/s3-monitoring/

# 3. Verify discovery works
zbx agent test <hostname> --from-check configs/checks/s3-monitoring/
```

## Script: getS3Storage.py

> **Note:** `getS3Storage.py` is environment-specific and is not committed to
> this repository.  Place your own implementation at
> `configs/checks/s3-monitoring/getS3Storage.py` before deploying.

The script must handle two calling conventions:

```bash
# Discovery — print LLD JSON
getS3Storage.py

# Per-user metrics — print JSON with keys: percentageused, usedspace, totalavailablesize
getS3Storage.py <user> <password> <group>
```

Expected JSON for metrics:

```json
{
  "percentageused": 42.5,
  "usedspace": 4294967296,
  "totalavailablesize": 10737418240
}
```
