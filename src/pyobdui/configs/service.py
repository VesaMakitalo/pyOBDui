"""Services for managing car configuration artifacts."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Mapping

from pydantic import ValidationError  # type: ignore[import]

from .constants import CONFIG_SUFFIX, DEFAULT_SUPPORTED_PIDS  # type: ignore[import]
from .models import CarConfig


class ConfigError(RuntimeError):
    """Base exception for configuration operations."""


class ConfigNotFoundError(ConfigError):
    """Raised when a requested configuration cannot be located."""


class ConfigDetectionError(ConfigError):
    """Raised when automatic PID detection fails."""


class ConfigService:
    """Manage reading, writing, and generating vehicle configurations."""

    _logger = logging.getLogger(__name__)

    def __init__(self, config_dir: Path, database_root: Path) -> None:
        self._config_dir = config_dir
        self._database_root = database_root
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._database_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def list_configs(self) -> List[CarConfig]:
        """Enumerate available configurations sorted by name."""

        configs: List[CarConfig] = []
        for path in sorted(self._config_dir.glob(f"*{CONFIG_SUFFIX}")):
            try:
                configs.append(CarConfig.model_validate_json(path.read_text()))
            except ValidationError as exc:
                self._logger.warning("Skipping invalid config %s: %s", path.name, exc)
        configs.sort(key=lambda cfg: cfg.name.lower())
        return configs

    def list_config_names(self) -> List[str]:
        """Return the names of known configurations."""

        return [config.name for config in self.list_configs()]

    def load_config(self, name: str) -> CarConfig:
        """Load a specific configuration by name."""

        path = self._config_path_for_name(name)
        if not path.exists():
            raise ConfigNotFoundError(f"Configuration '{name}' does not exist")
        try:
            return CarConfig.model_validate_json(path.read_text())
        except ValidationError as exc:
            raise ConfigError(f"Configuration '{name}' is invalid: {exc}") from exc

    def save_config(self, config: CarConfig) -> None:
        """Persist a configuration definition."""

        path = self._config_path_for_name(config.name)
        data = config.model_dump(mode="json")
        path.write_text(json.dumps(data, indent=2, sort_keys=True))
        self._logger.info("Saved configuration '%s' to %s", config.name, path)

    def delete_config(self, name: str) -> None:
        """Delete a configuration if it exists."""

        path = self._config_path_for_name(name)
        if path.exists():
            path.unlink()
            self._logger.info("Deleted configuration '%s'", name)

    def create_config(
        self,
        *,
        name: str,
        adapter_port: str,
        metadata: Mapping[str, str] | None = None,
        polling_interval: float = 1.0,
        auto_detect: bool = True,
        detection_timeout: float = 6.0,
    ) -> CarConfig:
        """Generate and persist a configuration, probing the adapter when possible."""

        supported_pids: List[str] = []
        if auto_detect:
            try:
                supported_pids = self.detect_supported_pids(adapter_port, timeout=detection_timeout)
            except ConfigDetectionError as exc:
                self._logger.warning("PID detection failed: %s", exc)
                supported_pids = self.default_supported_pids()
        else:
            supported_pids = self.default_supported_pids()

        config = CarConfig(
            name=name,
            adapter_port=adapter_port,
            database_path=self._database_path_for_name(name),
            supported_pids=supported_pids,
            polling_interval=polling_interval,
            metadata=dict(metadata or {}),
            created_at=datetime.now(timezone.utc),
        )

        self.save_config(config)
        return config

    def detect_supported_pids(
        self,
        port: str,
        *,
        timeout: float = 6.0,
        fast: bool = True,
    ) -> List[str]:
        """Attempt to connect to an adapter and enumerate supported commands."""

        try:
            import obd  # type: ignore[import]
        except ImportError as exc:  # pragma: no cover - dependency error surfaced at runtime
            raise ConfigDetectionError("python-OBD is not installed") from exc

        self._logger.info("Probing adapter on %s for supported PIDs", port)

        try:
            connection = obd.OBD(portstr=port, fast=fast, timeout=timeout)
        except Exception as exc:  # pragma: no cover - hardware dependent
            raise ConfigDetectionError(f"Failed to open adapter on {port}: {exc}") from exc

        if not connection.is_connected():
            connection.close()
            raise ConfigDetectionError(f"Adapter on {port} is not connected")

        try:
            commands = getattr(connection, "supported_commands", None)
            if not commands:
                raise ConfigDetectionError("Adapter did not report supported commands")

            names = sorted({cmd.name for cmd in commands if getattr(cmd, "name", None)})
            if not names:
                self._logger.warning("Adapter returned an empty command list; using defaults")
                return self.default_supported_pids()
            self._logger.info("Detected %d supported PIDs", len(names))
            return names
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def default_supported_pids(self) -> List[str]:
        """Return a baseline list of commonly-supported commands."""

        return list(DEFAULT_SUPPORTED_PIDS)

    def _config_path_for_name(self, name: str) -> Path:
        slug = self._slugify(name)
        return self._config_dir / f"{slug}{CONFIG_SUFFIX}"

    def _database_path_for_name(self, name: str) -> Path:
        slug = self._slugify(name)
        return (self._database_root / f"{slug}.db").resolve()

    @staticmethod
    def _slugify(name: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower())
        slug = slug.strip("-")
        return slug or "vehicle"
