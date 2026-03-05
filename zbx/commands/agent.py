"""zbx agent — deploy scripts and UserParameters to monitored hosts.

Commands:
    zbx agent deploy <host>   SSH into host, deploy scripts + userparameters.
    zbx agent diff   <host>   Show what would change without touching anything.
    zbx agent test   <host>   Run zabbix_agentd -t for each configured test key.

The agent config is defined in inventory.yaml under the `agent:` key of each
host entry. Example:

    hosts:
      - host: zabbixtest3100
        ip: 192.168.1.100
        agent:
          ssh_user: sanpau
          sudo: true
          scripts:
            - source: scripts/getS3Storage.py
              dest: /usr/local/scripts/zabbix/getS3Storage.py
          userparameters:
            - name: s3monitor
              parameters:
                - key: s3.user.discover
                  command: /usr/local/scripts/zabbix/getS3Storage.py
                - key: "s3.user.metrics[*]"
                  command: /usr/local/scripts/zabbix/getS3Storage.py $1 $2 $3
          restart_agent: false
          test_keys:
            - s3.user.discover
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table
from rich import print as rprint

from zbx.agent_deployer import AgentDeployer, AgentDiff
from zbx.config_loader import ConfigLoader

_loader = ConfigLoader()

app = typer.Typer(help="Deploy scripts and UserParameters to monitored hosts.")
console = Console()
log = logging.getLogger(__name__)

InventoryArg = Annotated[
    Path,
    typer.Argument(help="Path to inventory YAML file."),
]
HostArg = Annotated[str, typer.Argument(help="Hostname as defined in the inventory.")]


def _find_host(inventory_path: Path, hostname: str):
    inventory = _loader.load_inventory(inventory_path)
    for h in inventory.hosts:
        if h.host == hostname:
            return h
    raise typer.BadParameter(
        f"Host '{hostname}' not found in {inventory_path}. "
        f"Available: {[h.host for h in inventory.hosts]}"
    )


def _print_diff(d: AgentDiff) -> None:
    if not d.has_changes:
        rprint("[green]OK[/green] Agent is up-to-date — no changes needed.")
        return

    for s in d.scripts:
        if s.content_matches and s.owner_matches:
            rprint(f"  [dim]  (unchanged) {s.dest}[/dim]")
        elif not s.exists:
            rprint(f"  [green]+ (create)   {s.dest}[/green]  ← {s.source}")
        elif not s.content_matches:
            rprint(f"  [yellow]~ (update)   {s.dest}[/yellow]  ← {s.source}")
        elif not s.owner_matches:
            rprint(f"  [yellow]~ (chown)    {s.dest}[/yellow]")

    for u in d.userparameters:
        if u.content_matches:
            rprint(f"  [dim]  (unchanged) {u.remote_path}[/dim]")
        elif not u.exists:
            rprint(f"  [green]+ (create)   {u.remote_path}[/green]")
        else:
            rprint(f"  [yellow]~ (update)   {u.remote_path}[/yellow]")


# ---------------------------------------------------------------------------
# zbx agent diff <host>
# ---------------------------------------------------------------------------


@app.command("diff")
def agent_diff_cmd(
    hostname: HostArg,
    inventory: Annotated[Path, typer.Option("--inventory", "-i", help="Path to inventory YAML.")] = Path("inventory.yaml"),
) -> None:
    """Show what would change on the host agent without applying anything."""
    inv_host = _find_host(inventory, hostname)
    if inv_host.agent is None:
        rprint(f"[yellow]![/yellow]  No [bold]agent:[/bold] config for '{hostname}' in {inventory}.")
        raise typer.Exit(0)

    cfg = inv_host.agent
    repo_root = Path.cwd()

    rprint(f"\n[bold]Agent diff for [cyan]{hostname}[/cyan] ({inv_host.ip})[/bold]\n")
    with AgentDeployer(
        hostname=hostname,
        ip=inv_host.ip,
        ssh_user=cfg.ssh_user,
        ssh_port=cfg.ssh_port,
        ssh_key=cfg.ssh_key,
        sudo=cfg.sudo,
        repo_root=repo_root,
    ) as deployer:
        d = deployer.diff(cfg.scripts, cfg.userparameters)
    _print_diff(d)


# ---------------------------------------------------------------------------
# zbx agent deploy <host>
# ---------------------------------------------------------------------------


@app.command("deploy")
def agent_deploy_cmd(
    hostname: HostArg,
    inventory: Annotated[Path, typer.Option("--inventory", "-i", help="Path to inventory YAML.")] = Path("inventory.yaml"),
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without applying.")] = False,
    auto_approve: Annotated[bool, typer.Option("--auto-approve", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Deploy scripts and UserParameters to the host via SSH."""
    inv_host = _find_host(inventory, hostname)
    if inv_host.agent is None:
        rprint(f"[yellow]![/yellow]  No [bold]agent:[/bold] config for '{hostname}' in {inventory}.")
        rprint("  Add an [bold]agent:[/bold] section to this host entry to enable agent deployment.")
        raise typer.Exit(0)

    cfg = inv_host.agent
    repo_root = Path.cwd()

    rprint(f"\n[bold]Agent deploy → [cyan]{hostname}[/cyan] ({inv_host.ip})[/bold]\n")

    # For local deployments requiring sudo, prompt for password upfront
    from zbx.agent_deployer import _is_localhost  # noqa: PLC0415
    sudo_password: str | None = None
    if cfg.sudo and _is_localhost(inv_host.ip) and not dry_run:
        sudo_password = typer.prompt("sudo password", hide_input=True, default="", show_default=False)
        if not sudo_password:
            sudo_password = None

    with AgentDeployer(
        hostname=hostname,
        ip=inv_host.ip,
        ssh_user=cfg.ssh_user,
        ssh_port=cfg.ssh_port,
        ssh_key=cfg.ssh_key,
        sudo=cfg.sudo,
        sudo_password=sudo_password,
        repo_root=repo_root,
    ) as deployer:
        # Show diff first
        d = deployer.diff(cfg.scripts, cfg.userparameters)
        _print_diff(d)

        if not d.has_changes:
            raise typer.Exit(0)

        if dry_run:
            rprint("\n[dim](dry-run — no changes applied)[/dim]")
            raise typer.Exit(0)

        if not auto_approve:
            typer.confirm("\nApply these changes?", abort=True)

        rprint("\n[bold]Deploying…[/bold]")
        deployer.deploy(
            scripts=cfg.scripts,
            userparameters=cfg.userparameters,
            restart_agent=cfg.restart_agent,
            dry_run=False,
        )

        rprint("[green]OK[/green] Agent deploy complete.\n")

        if cfg.restart_agent:
            rprint("[dim]zabbix-agentd restarted.[/dim]")

        # Auto-run tests if configured
        if cfg.test_keys:
            rprint("\n[bold]Testing keys…[/bold]")
            _run_tests(deployer, cfg.test_keys)


# ---------------------------------------------------------------------------
# zbx agent test <host>
# ---------------------------------------------------------------------------


@app.command("test")
def agent_test_cmd(
    hostname: HostArg,
    inventory: Annotated[Path, typer.Option("--inventory", "-i", help="Path to inventory YAML.")] = Path("inventory.yaml"),
    key: Annotated[
        Optional[list[str]],
        typer.Option("--key", "-k", help="Extra keys to test (in addition to inventory config)."),
    ] = None,
) -> None:
    """Run zabbix_agentd -t for each key defined in the inventory agent config."""
    inv_host = _find_host(inventory, hostname)
    if inv_host.agent is None:
        rprint(f"[yellow]![/yellow]  No [bold]agent:[/bold] config for '{hostname}'.")
        raise typer.Exit(0)

    cfg = inv_host.agent
    keys_to_test = list(cfg.test_keys) + list(key or [])
    if not keys_to_test:
        rprint("[yellow]![/yellow]  No test_keys configured. Use --key <key> to test ad-hoc.")
        raise typer.Exit(0)

    rprint(f"\n[bold]Agent test → [cyan]{hostname}[/cyan] ({inv_host.ip})[/bold]\n")
    with AgentDeployer(
        hostname=hostname,
        ip=inv_host.ip,
        ssh_user=cfg.ssh_user,
        ssh_port=cfg.ssh_port,
        ssh_key=cfg.ssh_key,
        sudo=cfg.sudo,
    ) as deployer:
        _run_tests(deployer, keys_to_test)


def _run_tests(deployer: AgentDeployer, keys: list[str]) -> None:
    results = deployer.test_keys(keys)
    table = Table(show_header=True, header_style="bold")
    table.add_column("Key")
    table.add_column("Status")
    table.add_column("Output", overflow="fold")
    for k, ok, output in results:
        status = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
        table.add_row(k, status, output[:200])
    console.print(table)
