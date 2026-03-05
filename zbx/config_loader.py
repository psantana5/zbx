"""Load and validate YAML configuration files into Template, Host and Inventory models."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from zbx.models import Host, Inventory, Template, ZabbixSettings

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Loads Template and Host definitions from YAML files or directories."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_templates(self, path: Path) -> list[Template]:
        """Return all Template documents found at *path*."""
        templates, _ = self._load_all(path)
        return templates

    def load_hosts(self, path: Path) -> list[Host]:
        """Return all Host documents found at *path*."""
        _, hosts = self._load_all(path)
        return hosts

    def load_all(self, path: Path) -> tuple[list[Template], list[Host]]:
        """Return (templates, hosts) found at *path*."""
        return self._load_all(path)

    def load_inventory(self, path: Path) -> Inventory:
        """Load an inventory.yaml file."""
        try:
            with path.open() as fh:
                raw = yaml.safe_load(fh)
        except FileNotFoundError:
            raise FileNotFoundError(f"Inventory file not found: {path}") from None
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
        if raw is None:
            return Inventory()
        try:
            return Inventory.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"Inventory schema error in {path}:\n{exc}") from exc

    def load_settings(self, env_file: Path | None = None, profile: str | None = None) -> ZabbixSettings:
        """Load Zabbix connection settings from environment / .env file / profile.

        Resolution order (highest → lowest priority):
          1. ``profile`` name  → reads ``zbx.profiles.yaml`` in cwd
          2. ZBX_PROFILE env var (set by ``zbx --profile <name>``)
          3. ``env_file``      → loads a .env file
          4. OS environment    → ZBX_URL, ZBX_USER, ZBX_PASSWORD, …
        """
        import os

        effective_profile = profile or os.environ.get("ZBX_PROFILE")

        # 1. Profile overrides — read zbx.profiles.yaml and inject as env vars
        if effective_profile:
            self._apply_profile(effective_profile)
        elif env_file and env_file.exists():
            from dotenv import load_dotenv  # type: ignore[import-untyped]
            load_dotenv(env_file)
            logger.debug("Loaded environment from %s", env_file)

        missing = [v for v in ("ZBX_URL", "ZBX_PASSWORD") if not os.environ.get(v)]
        if missing:
            raise EnvironmentError(
                f"Missing required environment variable(s): {', '.join(missing)}\n"
                "Set them directly or create a .env file — see .env.example."
            )

        return ZabbixSettings(
            url=os.environ["ZBX_URL"],
            username=os.environ.get("ZBX_USER", "Admin"),
            password=os.environ["ZBX_PASSWORD"],
            verify_ssl=os.environ.get("ZBX_VERIFY_SSL", "true").lower() == "true",
            timeout=int(os.environ.get("ZBX_TIMEOUT", "30")),
        )

    def list_profiles(self, profiles_file: Path | None = None) -> dict[str, dict]:
        """Return all profiles defined in zbx.profiles.yaml (or empty dict)."""
        path = profiles_file or Path("zbx.profiles.yaml")
        if not path.exists():
            return {}
        try:
            with path.open() as fh:
                raw = yaml.safe_load(fh) or {}
            return raw.get("profiles", {})
        except yaml.YAMLError:
            return {}

    def _apply_profile(self, profile: str, profiles_file: Path | None = None) -> None:
        """Inject a named profile from zbx.profiles.yaml into os.environ."""
        import os

        profiles = self.list_profiles(profiles_file)
        if not profiles:
            raise EnvironmentError(
                "No zbx.profiles.yaml found in the current directory.\n"
                "Create one with your environment profiles — see README for format."
            )
        if profile not in profiles:
            available = ", ".join(profiles.keys())
            raise EnvironmentError(
                f"Profile '{profile}' not found. Available: {available}"
            )
        cfg = profiles[profile]
        mapping = {
            "url":        "ZBX_URL",
            "user":       "ZBX_USER",
            "password":   "ZBX_PASSWORD",
            "verify_ssl": "ZBX_VERIFY_SSL",
            "timeout":    "ZBX_TIMEOUT",
        }
        for key, env_var in mapping.items():
            if key in cfg:
                os.environ[env_var] = str(cfg[key])
        logger.debug("Applied profile '%s' → %s", profile, os.environ.get("ZBX_URL"))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_all(self, path: Path) -> tuple[list[Template], list[Host]]:
        files = self._collect_files(path)
        templates: list[Template] = []
        hosts: list[Host] = []
        seen_templates: dict[str, Path] = {}
        for f in files:
            t, h = self._load_file(f)
            for tmpl in t:
                if tmpl.template in seen_templates:
                    logger.warning(
                        "Duplicate template '%s' found in %s (already loaded from %s) — skipping",
                        tmpl.template, f, seen_templates[tmpl.template],
                    )
                    continue
                seen_templates[tmpl.template] = f
                templates.append(tmpl)
            hosts.extend(h)
        logger.debug(
            "Loaded %d template(s) and %d host(s) from %s",
            len(templates), len(hosts), path,
        )
        return templates, hosts

    def _collect_files(self, path: Path) -> list[Path]:
        if path.is_file():
            return [path]
        if path.is_dir():
            return sorted(path.rglob("*.yaml")) + sorted(path.rglob("*.yml"))
        raise FileNotFoundError(f"Path not found: {path}")

    def _load_file(self, path: Path) -> tuple[list[Template], list[Host]]:
        try:
            with path.open() as fh:
                docs = list(yaml.safe_load_all(fh))
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in {path}: {exc}") from exc

        # Filter out empty documents (e.g. trailing ---)
        docs = [d for d in docs if d is not None]
        if not docs:
            return [], []

        templates: list[Template] = []
        hosts: list[Host] = []

        for idx, doc in enumerate(docs):
            if not isinstance(doc, dict):
                raise ValueError(
                    f"Expected a mapping at document index {idx} in {path}, "
                    f"got {type(doc).__name__}"
                )
            try:
                if "host" in doc and "template" not in doc:
                    h = Host.model_validate(doc)
                    hosts.append(h)
                    logger.debug("Parsed host '%s' from %s", h.host, path)
                else:
                    t = Template.model_validate(doc)
                    templates.append(t)
                    logger.debug("Parsed template '%s' from %s", t.template, path)
            except ValidationError as exc:
                raise ValueError(
                    f"Schema validation failed for document {idx} in {path}:\n{exc}"
                ) from exc

        return templates, hosts
