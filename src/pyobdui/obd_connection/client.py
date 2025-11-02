"""OBD client built on python-OBD with asyncio integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

import obd  # type: ignore[import]

from ..configs import CarConfig
from ..db import DataRepository
from .constants import MIN_POLL_INTERVAL


class OBDClientError(RuntimeError):
    """Base exception for OBD client failures."""


class OBDConnectionError(OBDClientError):
    """Raised when a connection to the adapter cannot be established."""


class OBDClient:
    """Manage connections to an OBD-II adapter and stream data asynchronously."""

    _logger = logging.getLogger(__name__)

    def __init__(self, config: CarConfig, repository: DataRepository) -> None:
        self._config = config
        self._repository = repository

        self._connection: obd.OBD | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._subscriber_lock = asyncio.Lock()
        self._missing_pids: set[str] = set()

    async def __aenter__(self) -> "OBDClient":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def start(self) -> None:
        """Connect to the adapter and spawn the polling loop."""

        if self._poll_task is not None:
            return

        await self._repository.initialize()
        self._connection = await self._open_connection()
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="obd-poll-loop")
        self._logger.info("Started OBD polling loop for %s", self._config.name)

    async def stop(self) -> None:
        """Stop polling and close the adapter connection."""

        if self._poll_task is not None:
            self._stop_event.set()
            try:
                await self._poll_task
            finally:
                self._poll_task = None

        if self._connection is not None:
            await asyncio.to_thread(self._connection.close)
            self._connection = None
            self._logger.info("Closed OBD connection for %s", self._config.name)

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        """Yield samples in real-time for consumers such as the UI."""

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        async with self._subscriber_lock:
            self._subscribers.append(queue)

        try:
            while True:
                sample = await queue.get()
                yield sample
        finally:
            async with self._subscriber_lock:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)

    async def read_dtcs(self, *, persist: bool = True) -> list[tuple[str, str | None]]:
        """Retrieve diagnostic trouble codes from the vehicle."""

        connection = await self._ensure_connection()
        response = await asyncio.to_thread(connection.query, obd.commands.GET_DTC)
        codes: list[tuple[str, str | None]] = []

        if response and not response.is_null() and response.value:
            # python-OBD returns list[tuple[str, str]]
            codes = [(code, desc or None) for code, desc in response.value]
            if persist:
                await self._repository.append_dtc_codes(codes, cleared=False)
        return codes

    async def clear_dtcs(self) -> None:
        """Clear diagnostic trouble codes from the vehicle."""

        connection = await self._ensure_connection()
        await asyncio.to_thread(connection.query, obd.commands.CLEAR_DTC)
        self._logger.info("Requested DTC clear for %s", self._config.name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _open_connection(self) -> obd.OBD:
        self._logger.info("Connecting to adapter on %s", self._config.adapter_port)

        def _connect() -> obd.OBD:
            return obd.OBD(portstr=self._config.adapter_port, fast=True)

        connection = await asyncio.to_thread(_connect)
        if not connection.is_connected():
            connection.close()
            raise OBDConnectionError(
                f"Unable to establish OBD connection on {self._config.adapter_port}"
            )
        return connection

    async def _ensure_connection(self) -> obd.OBD:
        if self._connection is None:
            self._connection = await self._open_connection()
        return self._connection

    async def _poll_loop(self) -> None:
        assert self._connection is not None
        interval = max(self._config.polling_interval, MIN_POLL_INTERVAL)

        try:
            while not self._stop_event.is_set():
                samples = await self._collect_samples()
                if samples:
                    await self._repository.insert_samples(samples)
                    await self._broadcast(samples)

                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    continue
        except Exception:  # pragma: no cover - guard unexpected failures
            self._logger.exception("Unexpected error in OBD polling loop")
        finally:
            self._stop_event.set()

    async def _collect_samples(self) -> List[dict[str, Any]]:
        connection = await self._ensure_connection()
        samples: List[dict[str, Any]] = []

        for pid_name in self._config.sorted_pids():
            command = self._resolve_command(pid_name)
            if command is None:
                continue

            try:
                response = await asyncio.to_thread(connection.query, command)
            except Exception as exc:  # pragma: no cover - hardware specific failures
                self._logger.warning("Query for %s failed: %s", pid_name, exc)
                continue

            sample = self._serialize_response(command, response)
            if sample:
                samples.append(sample)
        return samples

    def _resolve_command(self, pid_name: str):
        command = getattr(obd.commands, pid_name, None)
        if command is None and pid_name not in self._missing_pids:
            self._logger.warning("Unsupported PID '%s' encountered; skipping", pid_name)
            self._missing_pids.add(pid_name)
        return command

    async def _broadcast(self, samples: Iterable[dict[str, Any]]) -> None:
        async with self._subscriber_lock:
            if not self._subscribers:
                return

            for sample in samples:
                for queue in self._subscribers:
                    try:
                        queue.put_nowait(sample)
                    except asyncio.QueueFull:
                        # Drop oldest item to make room, then retry.
                        try:
                            queue.get_nowait()
                            queue.put_nowait(sample)
                        except asyncio.QueueEmpty:
                            pass

    def _serialize_response(self, command: obd.OBDCommand, response: Any) -> Dict[str, Any]:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

        sample: Dict[str, Any] = {
            "pid": command.name,
            "description": getattr(command, "description", ""),
            "recorded_at": timestamp,
            "status": "ok",
        }

        if response is None or getattr(response, "is_null", lambda: True)():
            sample["status"] = "no_data"
            return sample

        value = getattr(response, "value", None)
        sample["raw"] = str(value)
        sample["unit"] = _extract_unit(value)
        sample["value"] = _extract_numeric(value)
        sample["display"] = str(value)

        return sample


def _extract_unit(value: Any) -> str | None:
    unit = getattr(value, "units", None)
    if unit:
        return str(unit)
    return None


def _extract_numeric(value: Any) -> float | None:
    if value is None:
        return None
    if hasattr(value, "magnitude"):
        try:
            return float(getattr(value, "magnitude"))
        except (TypeError, ValueError):
            return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
