"""Agent deployer — deploys scripts and UserParameters to monitored hosts.

Supports two execution modes selected automatically:
  - **Local**  when host IP is 127.0.0.1 / localhost / ::1
               → uses subprocess, no SSH needed
  - **Remote** all other IPs
               → uses paramiko SSH + SFTP

Architecture:
    AgentDeployer.connect()      → opens SSH (remote) or no-op (local)
    AgentDeployer.diff()         → compare local repo vs host, no writes
    AgentDeployer.deploy()       → copy scripts, write userparameters
    AgentDeployer.test_keys()    → run zabbix_agentd -t <key> on host
    AgentDeployer.close()        → close SSH (remote) or no-op (local)

The deployer is script-agnostic — it handles any scripts and UserParameter
keys defined in inventory.yaml, regardless of what they monitor.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# IPs that mean "this machine" — skip SSH and run locally
_LOCALHOST = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})


def _is_localhost(ip: str) -> bool:
    return ip.lower() in _LOCALHOST


# ---------------------------------------------------------------------------
# Diff structures
# ---------------------------------------------------------------------------


@dataclass
class ScriptStatus:
    source: str
    dest: str
    exists: bool
    content_matches: bool
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

    @property
    def has_changes(self) -> bool:
        return (
            any(not s.content_matches or not s.owner_matches for s in self.scripts)
            or any(not u.content_matches for u in self.userparameters)
        )


# ---------------------------------------------------------------------------
# Main deployer
# ---------------------------------------------------------------------------


class AgentDeployer:
    """Deploy scripts and UserParameters to a host — local or remote."""

    def __init__(
        self,
        hostname: str,
        ip: str,
        ssh_user: str = "root",
        ssh_port: int = 22,
        ssh_key: str | None = None,
        sudo: bool = True,
        sudo_password: str | None = None,
        repo_root: Path | None = None,
    ) -> None:
        self.hostname = hostname
        self.ip = ip
        self.ssh_user = ssh_user
        self.ssh_port = ssh_port
        self.ssh_key = ssh_key
        self.sudo = sudo
        self.sudo_password = sudo_password
        self.repo_root = repo_root or Path.cwd()
        self._local: bool = _is_localhost(ip)
        self._ssh = None
        self._sftp = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        if self._local:
            log.debug("Localhost detected (%s) — skipping SSH", self.ip)
            return
        try:
            import paramiko  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "paramiko is required for remote agent deployment: pip install paramiko"
            ) from exc
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw: dict = dict(hostname=self.ip, port=self.ssh_port, username=self.ssh_user, timeout=30)
        if self.ssh_key:
            kw["key_filename"] = self.ssh_key
        log.debug("SSH connect → %s@%s:%d", self.ssh_user, self.ip, self.ssh_port)
        client.connect(**kw)
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
    # Command execution (local or remote)
    # ------------------------------------------------------------------

    def _run(self, cmd: str, check: bool = True) -> tuple[int, str, str]:
        """Run a shell command locally or over SSH."""
        if self._local:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            code, out, err = result.returncode, result.stdout.strip(), result.stderr.strip()
        else:
            _, stdout, stderr = self._ssh.exec_command(cmd)
            code = stdout.channel.recv_exit_status()
            out = stdout.read().decode(errors="replace").strip()
            err = stderr.read().decode(errors="replace").strip()
        log.debug("[%s] $ %s  → %d", "local" if self._local else "ssh", cmd, code)
        if check and code != 0:
            raise RuntimeError(f"Command failed ({code}): {cmd}\n{err}")
        return code, out, err

    def _sudo(self, cmd: str, check: bool = True) -> tuple[int, str, str]:
        if not self.sudo:
            return self._run(cmd, check=check)
        if self._local and self.sudo_password is not None:
            # Use sudo -S to read password from stdin — avoids interactive prompts
            escaped = self.sudo_password.replace("'", "'\\''")
            return self._run(f"echo '{escaped}' | sudo -S -p '' {cmd}", check=check)
        return self._run(f"sudo {cmd}", check=check)

    # ------------------------------------------------------------------
    # File inspection helpers
    # ------------------------------------------------------------------

    def _sha256_of_path(self, path: str) -> str | None:
        """SHA-256 of a file on the host (local or remote)."""
        if self._local:
            p = Path(path)
            if not p.exists():
                return None
            return hashlib.sha256(p.read_bytes()).hexdigest()
        code, out, _ = self._run(f"sha256sum {path} 2>/dev/null || true", check=False)
        return out.split()[0] if (code == 0 and out) else None

    def _read_path(self, path: str) -> str | None:
        """Read text content of a file on the host."""
        if self._local:
            p = Path(path)
            if not p.exists():
                return None
            try:
                return p.read_text(errors="replace")
            except PermissionError:
                # Try via sudo cat for root-owned files like /etc/zabbix/...
                code, out, _ = self._run(f"sudo cat {path} 2>/dev/null || true", check=False)
                return out if code == 0 else None
        code, out, _ = self._run(f"cat {path} 2>/dev/null || true", check=False)
        return out if code == 0 else None

    def _owner_of(self, path: str) -> str:
        code, out, _ = self._run(f"stat -c '%U' {path} 2>/dev/null || true", check=False)
        return out.strip() if code == 0 else ""

    def _local_sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    # ------------------------------------------------------------------
    # File upload
    # ------------------------------------------------------------------

    def _upload(self, local_path: Path, dest: str, owner: str, group: str, mode: str) -> None:
        """Copy local_path to dest on the host with correct ownership/permissions."""
        dest_dir = str(Path(dest).parent)
        self._sudo(f"mkdir -p {dest_dir}")

        if self._local:
            # Copy to a tmp file owned by current user, then sudo mv into place
            tmp = f"/tmp/_zbx_{local_path.name}"
            shutil.copy2(str(local_path), tmp)
            self._sudo(f"mv {tmp} {dest}")
        else:
            tmp = f"/tmp/_zbx_{local_path.name}"
            self._sftp.put(str(local_path), tmp)
            self._sudo(f"mv {tmp} {dest}")

        self._sudo(f"chown {owner}:{group} {dest}")
        self._sudo(f"chmod {mode} {dest}")

    def _write_text(self, content: str, dest: str) -> None:
        """Write text content to dest on the host."""
        import tempfile  # noqa: PLC0415
        dest_dir = str(Path(dest).parent)
        self._sudo(f"mkdir -p {dest_dir}")
        if self._local:
            # Write to a tmp file without sudo, then sudo cp into place
            with tempfile.NamedTemporaryFile(mode="w", suffix=".zbxtmp", delete=False) as tf:
                tf.write(content)
                tmp_path = tf.name
            try:
                self._sudo(f"cp {tmp_path} {dest}")
                self._sudo(f"chmod 644 {dest}")
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        else:
            tmp = f"/tmp/_zbx_{Path(dest).name}"
            _, stdout_obj, _ = self._ssh.exec_command(f"cat > {tmp}")
            stdout_obj.channel.sendall(content.encode())
            stdout_obj.channel.shutdown_write()
            stdout_obj.channel.recv_exit_status()
            self._sudo(f"mv {tmp} {dest}")
            self._sudo(f"chmod 644 {dest}")

    # ------------------------------------------------------------------
    # Diff
    # ------------------------------------------------------------------

    def diff(self, scripts: list, userparameters: list) -> AgentDiff:
        result = AgentDiff(host=self.hostname)

        for s in scripts:
            local_path = self.repo_root / s.source
            if not local_path.exists():
                log.warning("Local script not found: %s", local_path)
                result.scripts.append(
                    ScriptStatus(s.source, s.dest, exists=False, content_matches=False, owner_matches=False)
                )
                continue
            remote_hash = self._sha256_of_path(s.dest)
            local_hash = self._local_sha256(local_path)
            exists = remote_hash is not None
            content_matches = remote_hash == local_hash
            owner_matches = (self._owner_of(s.dest) == s.owner) if exists else False
            result.scripts.append(
                ScriptStatus(s.source, s.dest, exists=exists,
                             content_matches=content_matches, owner_matches=owner_matches)
            )

        for up in userparameters:
            expected = _render_userparameters(up)
            remote_content = self._read_path(up.remote_path)
            exists = remote_content is not None
            content_matches = (remote_content == expected) if exists else False
            result.userparameters.append(
                UserParamStatus(up.remote_path, exists=exists, content_matches=content_matches)
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
                self._upload(local_path, s.dest, s.owner, s.group, s.mode)

        for idx, up in enumerate(userparameters):
            status = d.userparameters[idx]
            if status.content_matches:
                log.debug("UserParameters up-to-date: %s", up.remote_path)
                continue
            log.info("Writing UserParameters → %s", up.remote_path)
            if not dry_run:
                self._write_text(_render_userparameters(up), up.remote_path)

        if restart_agent and d.has_changes and not dry_run:
            log.info("Restarting zabbix-agentd on %s", self.hostname)
            self._sudo("systemctl restart zabbix-agentd")

        return d

    # ------------------------------------------------------------------
    # Test
    # ------------------------------------------------------------------

    def test_keys(self, keys: list[str]) -> list[tuple[str, bool, str]]:
        """Run ``zabbix_agentd -t <key>``; return (key, ok, output) for each."""
        results = []
        for key in keys:
            code, out, err = self._run(f"zabbix_agentd -t {key} 2>&1", check=False)
            results.append((key, code == 0, out or err))
        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_userparameters(up) -> str:
    """Render a UserParametersFile as the .conf file text content."""
    lines = [
        "# Managed by zbx — do not edit manually",
        f"# Source: zbx inventory agent.userparameters[{up.name}]",
        "",
    ]
    for p in up.parameters:
        lines.append(f"UserParameter={p.key},{p.command}")
    return "\n".join(lines) + "\n"



