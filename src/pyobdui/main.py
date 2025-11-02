"""Application entry point for pyOBDui."""

from __future__ import annotations

import asyncio
import logging
import sys
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import suppress
from pathlib import Path
from threading import Thread
from typing import Dict, Optional

from .common import configure_logging
from .configs import CarConfig, ConfigService
from .constants import DEFAULT_ADAPTER_PORT  # type: ignore[import]
from .db import DataRepository
from .obd_connection import OBDClient, OBDConnectionError
from .ui import MonitoringApp


def main() -> None:
    """CLI entry point that guides the user through configuration selection."""

    configure_logging()
    logger = logging.getLogger(__name__)

    project_root = Path(__file__).resolve().parents[2]
    data_root = project_root / "data"
    config_dir = data_root / "configs"
    database_root = data_root / "databases"

    service = ConfigService(config_dir, database_root)

    try:
        config = prompt_for_configuration(service)
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user.")
        return

    logger.info("")
    logger.info("Selected configuration: %s", config.name)
    logger.info("Adapter port: %s", config.adapter_port)
    logger.info("Database path: %s", config.database_path)
    logger.info("Polling interval: %ss", config.polling_interval)
    if config.metadata:
        logger.info("Metadata:")
        for key, value in sorted(config.metadata.items()):
            logger.info("  %s: %s", key, value)

    run_monitoring_session(config)


# ---------------------------------------------------------------------------
# Interactive configuration helpers
# ---------------------------------------------------------------------------


def prompt_for_configuration(service: ConfigService) -> CarConfig:
    """List known configs and optionally create a new one."""

    logger = logging.getLogger(__name__)
    while True:
        configs = service.list_configs()
        logger.info("\nAvailable vehicle configurations:")
        if configs:
            for idx, cfg in enumerate(configs, start=1):
                logger.info("  %d. %s (port=%s)", idx, cfg.name, cfg.adapter_port)
        else:
            logger.info("  [none]")

        logger.info("\nOptions: [number] select, [n] new config, [q] quit")
        choice = input("> ").strip().lower()

        if choice in {"q", "quit"}:
            logger.info("Exiting without selecting a configuration.")
            sys.exit(0)

        if choice in {"n", "new"}:
            return create_configuration(service)

        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(configs):
                return configs[index]
            logger.warning("Invalid selection: index out of range.")
            continue

        logger.warning("Unrecognized option. Please try again.")


def create_configuration(service: ConfigService) -> CarConfig:
    """Interactively gather details and generate a new configuration."""

    logger = logging.getLogger(__name__)
    logger.info("\nCreating a new vehicle configuration.")
    name = _prompt_non_empty("Vehicle name")
    port = input(f"Adapter port [{DEFAULT_ADAPTER_PORT}]: ").strip() or DEFAULT_ADAPTER_PORT

    polling_interval = _prompt_polling_interval()
    metadata = _prompt_metadata()

    logger.info("\nDetecting supported PIDs (this may take a few seconds)...")
    config = service.create_config(
        name=name,
        adapter_port=port,
        metadata=metadata,
        polling_interval=polling_interval,
    )

    logger.info(
        "Created configuration '%s' with %d PIDs.",
        config.name,
        len(config.supported_pids),
    )
    return config


def _prompt_non_empty(label: str) -> str:
    logger = logging.getLogger(__name__)
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value
        logger.warning("Value cannot be empty. Please try again.")


def _prompt_polling_interval() -> float:
    default = 1.0
    raw = input(f"Polling interval seconds [{default}]: ").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logging.getLogger(__name__).warning("Invalid number; using default of 1.0 seconds.")
        return default
    if value <= 0:
        logging.getLogger(__name__).warning(
            "Polling interval must be positive; using default of 1.0 seconds."
        )
        return default
    return value


def _prompt_metadata() -> Dict[str, str]:
    logger = logging.getLogger(__name__)
    logger.info("\nEnter optional metadata as key=value pairs (blank line to finish).")
    metadata: Dict[str, str] = {}
    while True:
        entry = input("metadata> ").strip()
        if not entry:
            break
        if "=" not in entry:
            logger.warning("Please use the form key=value.")
            continue
        key, value = (segment.strip() for segment in entry.split("=", 1))
        if not key:
            logger.warning("Key cannot be empty.")
            continue
        metadata[key] = value
    return metadata


# ---------------------------------------------------------------------------
# Monitoring session orchestration
# ---------------------------------------------------------------------------


def run_monitoring_session(config: CarConfig) -> None:
    """Initialize background services and launch the monitoring UI."""

    logger = logging.getLogger(__name__)
    loop = asyncio.new_event_loop()
    worker = _AsyncioWorker(loop)
    worker.start()

    repository = DataRepository(config.database_path)
    _run_coroutine(loop, repository.initialize(), timeout=10)

    obd_client = OBDClient(config, repository)
    obd_active = False

    try:
        _run_coroutine(loop, obd_client.start(), timeout=10)
        obd_active = True
    except OBDConnectionError as exc:
        logger.error("Unable to connect to adapter: %s", exc)
        if not _ask_yes_no("Continue without a live OBD connection? [y/N]: ", default=False):
            _shutdown(loop, worker, repository, obd_client if obd_active else None)
            return
        logger.warning(
            "Continuing in offline mode. Telemetry will not update until a connection is established."
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected error while starting OBD client")
        logger.error("Failed to start OBD client: %s", exc)
        _shutdown(loop, worker, repository, obd_client if obd_active else None)
        return

    app = MonitoringApp(repository, obd_client if obd_active else None, loop)

    try:
        exit_code = app.run()
        if exit_code:
            logger.info("Application exited with status %s", exit_code)
    finally:
        _shutdown(loop, worker, repository, obd_client if obd_active else None)


def _shutdown(
    loop: asyncio.AbstractEventLoop,
    worker: "_AsyncioWorker",
    repository: DataRepository,
    obd_client: Optional[OBDClient],
) -> None:
    """Gracefully stop background services and the asyncio loop."""

    if obd_client is not None:
        with suppress(Exception):
            _run_coroutine(loop, obd_client.stop(), timeout=5)

    with suppress(Exception):
        _run_coroutine(loop, repository.close(), timeout=5)

    worker.stop()


def _run_coroutine(
    loop: asyncio.AbstractEventLoop,
    coro: asyncio.Awaitable[object],
    *,
    timeout: float,
):
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError:
        future.cancel()
        raise


def _ask_yes_no(prompt: str, *, default: bool) -> bool:
    default_str = "y" if default else "n"
    while True:
        choice = input(prompt).strip().lower()
        if not choice:
            return default
        if choice in {"y", "yes"}:
            return True
        if choice in {"n", "no"}:
            return False
        logging.getLogger(__name__).warning("Please answer 'y' or 'n'.")


class _AsyncioWorker(Thread):
    """Run an asyncio event loop in a dedicated daemon thread."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(daemon=True)
        self._loop = loop

    def run(self) -> None:  # pragma: no cover - thread startup
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def stop(self) -> None:
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self.join(timeout=5)


if __name__ == "__main__":
    main()
