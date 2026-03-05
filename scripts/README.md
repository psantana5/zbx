# scripts/

This directory contains **Zabbix agent scripts** that zbx deploys to monitored
hosts via `zbx agent deploy`.

Each script here corresponds to a `source:` path in the inventory `agent.scripts`
config. zbx compares the local SHA-256 against the remote file and only
deploys when there are changes — exactly like `rsync --checksum`.

## Usage

```yaml
# inventory.yaml
hosts:
  - host: myhost
    ip: 192.168.1.100
    agent:
      ssh_user: sanpau
      scripts:
        - source: scripts/getS3Storage.py        # ← path relative to repo root
          dest: /usr/local/scripts/zabbix/getS3Storage.py
          owner: zabbix
          group: zabbix
          mode: "0755"
```

```bash
zbx agent diff   myhost   # see what would change
zbx agent deploy myhost   # deploy
zbx agent test   myhost   # run zabbix_agentd -t for configured test_keys
```

## Files

| File | Description |
|------|-------------|
| `getS3Storage.py` | S3 user storage metrics (add your copy here) |

Place the actual script files here and commit them to Git.
zbx will track changes and redeploy only when the content differs.
