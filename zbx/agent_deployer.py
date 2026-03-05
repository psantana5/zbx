"""SSH-based agent deployer.

Deploys scripts and UserParameters configuration files to monitored hosts,
then optionally restarts the Zabbix agent and verifies keys with
``zabbix_agentd -t``.

Architecture:
    AgentDeployer.connect()          → opens an SSH + SFTP session
    AgentDeployer.diff()             → returns AgentDiff without touching host
    AgentDeployer.deploy(dry_run)    → transfers files and writes configs
    AgentDeployer.test_keys()        → runs zabbix_agentd -t <key> for each key
    AgentDeployer.close()            → closes SSH session
"""

from __future__ import annotations

import logging
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Diff structures
# ---------------------------------------------------------------------------


@dataclass
class ScriptStatus:
    source: str
    dest: str
    exists: bool
    content_matches: bool  # False if file is absent or hash differs
    owner_matches: bool


@dataclass
class UserParamStatus:
    remote_path: str
    exists: bool
    content_matches: bool


@dataclass
class AgentDiff:
    host: str
    scripts: list[ScriptStatus] = field(default_factory=list)
    userparameters: list[UserParamStatus] = field(default_factory=list)
    restart_needed: bool = False

    @property
    def has_changes(self) -> bool:
        return (
            any(not s.content_matches for s in self.scripts)
            or any(not u.content_matches for u in self.userparameters)
        )


# ---------------------------------------------------------------------------
# Main deployer
# ---------------------------------------------------------------------------


class AgentDeployer:
    """Manages SSH connection and file deployment to a single host."""

    def __init__(
        self,
        hostname: str,
        ip: str,
        ssh_user: str,
        ssh_port: int = 22,
        ssh_key: str | None = None,
        sudo: bool = True,
        repo_root: Path | None = None,
    ) -> None:
        self.hostname = hostname
        self.ip = ip
        self.ssh_user = ssh_user
        self.ssh_port = ssh_port
        self.ssh_key = ssh_key
        self.sudo = sudo
        self.repo_root = repo_root or Path.cwd()
        self._ssh = None
        self._sftp = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        try:
            import paramiko  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "paramiko is required for agent deployment: pip install paramiko"
            ) from exc

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: dict = dict(
            hostname=self.ip,
            port=self.ssh_port,
            username=self.ssh_user,
            timeout=30,
        )
        if self.ssh_key:
            connect_kwargs["key_filename"] = self.ssh_key
        log.debug("SSH connect → %s@%s:%d", self.ssh_user, self.ip, self.ssh_port)
        client.connect(**connect_kwargs)
        self._ssh = client
        self._sftp = client.open_sftp()

    def close(self) -> None:
        if self._sftp:
            self._sftp.close()
        if self._ssh:
            self._ssh.close()

    def __enter__(self) -> AgentDeployer:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, cmd: str, check: bool = True) -> tuple[int, str, str]:
        """Run a command over SSH; return (exit_code, stdout, stderr)."""
        _, stdout, stderr = self._ssh.exec_command(cmd)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        log.debug("$ %s  → %d", cmd, exit_code)
        if check and exit_code != 0:
            raise RuntimeError(f"Remote command failed ({exit_code}): {cmd}\n{err}")
        return exit_code, out, err

    def _sudo(self, cmd: str, check: bool = True) -> tuple[int, str, str]:
        full = f"sudo {cmd}" if self.sudo else cmd
        return self._run(full, check=check)

    def _remote_sha256(self, path: str) -> str | None:
        code, out, _ = self._run(f"sha256sum {path} 2>/dev/null || true", check=False)
        if code != 0 or not out:
            return None
        return out.split()[0]

    def _local_sha256(self, path: Path) -> str:
        import hashlib
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _remote_file_content(self, path: str) -> str | None:
        code, out, _ = self._run(f"cat {path} 2>/dev/null || true", check=False)
        return out if code == 0 else None

    # ------------------------------------------------------------------
    # Diff
    # ------------------------------------------------------------------

    def diff(
        self,
        scripts: list,
        userparameters: list,
    ) -> AgentDiff:
        result = AgentDiff(host=self.hostname)
        for s in scripts:
            local_path = self.repo_root / s.source
            if not local_path.exists():
                log.warning("Local script not found: %s", local_path)
                result.scripts.append(
                    ScriptStatus(s.source, s.dest, exists=False, content_matches=False, owner_matches=False)
                )
                continue
            remote_hash = self._remote_sha256(s.dest)
            local_hash = self._local_sha256(local_path)
            exists = remote_hash is not None
            matches = remote_hash == local_hash
            # Check owner (best-effort)
            owner_ok = True
            if exists:
                _, owner_out, _ = self._run(f"stat -c '%U' {s.dest} 2>/dev/null || true", check=False)
                owner_ok = owner_out.strip() == s.owner
            result.scripts.append(
                ScriptStatus(s.source, s.dest, exists=exists, content_matches=matches, owner_matches=owner_ok)
            )

        for up in userparameters:
            remote_path = up.remote_path
            expected = _render_userparameters(up)
            remote_content = self._remote_file_content(remote_path)
            exists = remote_content is not None
            matches = remote_content == expected if exists else False
            result.userparameters.append(
                UserParamStatus(remote_path, exists=exists, content_matches=matches)
            )
        return result

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    def deploy(
        self,
        scripts: list,
        userparameters: list,
        restart_agent: bool = False,
        dry_run: bool = False,
    ) -> AgentDiff:
        """Deploy scripts and userparameters. Returns the diff (changes made)."""
        d = self.diff(scripts, userparameters)

        for idx, s in enumerate(scripts):
            status = d.scripts[idx]
            if status.content_matches and status.owner_matches:
                log.debug("Script up-to-date: %s", s.dest)
                continue
            local_path = self.repo_root / s.source
            if not local_path.exists():
                log.warning("Skipping missing local script: %s", local_path)
                continue
            log.info("Deploying script %s → %s", s.source, s.dest)
            if not dry_run:
                # Ensure destination directory exists
                dest_dir = str(Path(s.dest).parent)
                self._sudo(f"mkdir -p {dest_dir}")
                # Upload to tmp then sudo mv (SFTP may lack permission to dest dir)
                tmp = f"/tmp/_zbx_{Path(s.source).name}"
                self._sftp.put(str(local_path), tmp)
                self._sudo(f"mv {tmp} {s.dest}")
                self._sudo(f"chown {s.owner}:{s.group} {s.dest}")
                self._sudo(f"chmod {s.mode} {s.dest}")

        for idx, up in enumerate(userparameters):
            status = d.userparameters[idx]
            if status.content_matches:
                log.debug("UserParameters up-to-date: %s", up.remote_path)
                continue
            content = _render_userparameters(up)
            log.info("Writing UserParameters → %s", up.remote_path)
            if not dry_run:
                # Ensure directory exists
                up_dir = str(Path(up.remote_path).parent)
                self._sudo(f"mkdir -p {up_dir}")
                # Write via tee (works with sudo)
                escaped = content.replace("'", "'\\''")
                self._sudo(f"bash -c \"echo '{escaped}' | tee {up.remote_path} > /dev/null\"")
                self._sudo(f"chmod 644 {up.remote_path}")

        if restart_agent and d.has_changes and not dry_run:
            log.info("Restarting zabbix-agentd on %s", self.hostname)
            self._sudo("systemctl restart zabbix-agentd")

        return d

    # ------------------------------------------------------------------
    # Test
    # ------------------------------------------------------------------

    def test_keys(self, keys: list[str]) -> list[tuple[str, bool, str]]:
        """Run ``zabbix_agentd -t <key>`` for each key; return (key, ok, output)."""
        results = []
        for key in keys:
            code, out, err = self._run(f"zabbix_agentd -t {key} 2>&1", check=False)
            ok = code == 0
            output = out or err
            log.debug("zabbix_agentd -t %s  → %s", key, "OK" if ok else "FAIL")
            results.append((key, ok, output))
        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_userparameters(up) -> str:
    """Render a UserParametersFile as the text content of the .conf file."""
    lines = [
        f"# Managed by zbx — do not edit manually",
        f"# Source: zbx inventory agent.userparameters[{up.name}]",
        "",
    ]
    for p in up.parameters:
        lines.append(f"UserParameter={p.key},{p.command}")
    return "\n".join(lines) + "\n"
