"""Load and validate YAML configuration files into Template models."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from zbx.models import Template, ZabbixSettings

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Loads Template definitions from YAML files or directories."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_templates(self, path: Path) -> list[Template]:
        """Return all templates found at *path* (file or directory tree)."""
        files = self._collect_files(path)
        templates: list[Template] = []
        for f in files:
            templates.extend(self._load_file(f))
        logger.debug("Loaded %d template(s) from %s", len(templates), path)
        return templates

    def load_settings(self, env_file: Path | None = None) -> ZabbixSettings:
        """Load Zabbix connection settings from environment / .env file."""
        import os

        if env_file and env_file.exists():
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_files(self, path: Path) -> list[Path]:
        if path.is_file():
            return [path]
        if path.is_dir():
            return sorted(path.rglob("*.yaml")) + sorted(path.rglob("*.yml"))
        raise FileNotFoundError(f"Path not found: {path}")

    def _load_file(self, path: Path) -> list[Template]:
        try:
            with path.open() as fh:
                raw = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in {path}: {exc}") from exc

        if raw is None:
            return []

        documents = raw if isinstance(raw, list) else [raw]
        templates: list[Template] = []
        for idx, doc in enumerate(documents):
            if not isinstance(doc, dict):
                raise ValueError(
                    f"Expected a mapping at document index {idx} in {path}, got {type(doc).__name__}"
                )
            try:
                tmpl = Template.model_validate(doc)
                templates.append(tmpl)
                logger.debug("Parsed template '%s' from %s", tmpl.template, path)
            except ValidationError as exc:
                # Re-raise with context so callers can show a clean error.
                raise ValueError(
                    f"Schema validation failed for document {idx} in {path}:\n{exc}"
                ) from exc
        return templates
