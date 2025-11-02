"""Async database repository for telemetry and diagnostic data."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence, Tuple

import aiosqlite  # type: ignore[import]

from .constants import DTC_HISTORY, DTC_INSERT, TELEMETRY_INSERT, TELEMETRY_LATEST


@dataclass(slots=True)
class DTCRecord:
    """A record representing a diagnostic trouble code event."""

    code: str
    description: str | None
    detected_at: datetime
    cleared: bool


class DataRepository:
    """Persist and retrieve telemetry and diagnostic data using SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Prepare the database connection and ensure schema exists."""

        await self._ensure_connection()
        assert self._connection is not None  # for type checkers

        await self._connection.execute("PRAGMA journal_mode=WAL;")
        await self._connection.execute("PRAGMA foreign_keys=ON;")
        await self._connection.execute("PRAGMA synchronous=NORMAL;")

        await self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS telemetry_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pid TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                value_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_telemetry_samples_pid_time
                ON telemetry_samples (pid, recorded_at DESC);

            CREATE TABLE IF NOT EXISTS dtc_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                description TEXT,
                detected_at TEXT NOT NULL,
                cleared INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_dtc_events_detected_at
                ON dtc_events (detected_at DESC);
            """
        )

        await self._connection.commit()

    async def insert_samples(self, samples: Iterable[dict[str, Any]]) -> None:
        """Insert a batch of sensor samples."""

        sample_list = list(samples)
        if not sample_list:
            return

        await self._ensure_connection()
        async with self._lock:
            assert self._connection is not None
            values = [
                (
                    sample.get("pid"),
                    _ensure_iso_timestamp(sample.get("recorded_at")),
                    json.dumps(sample, default=str),
                )
                for sample in sample_list
            ]

            await self._connection.executemany(TELEMETRY_INSERT, values)
            await self._connection.commit()

    async def fetch_latest_samples(self) -> list[dict[str, Any]]:
        """Fetch the most recent sample for each PID."""

        await self._ensure_connection()
        async with self._lock:
            assert self._connection is not None
            cursor = await self._connection.execute(TELEMETRY_LATEST)
            rows = await cursor.fetchall()
            await cursor.close()

        samples: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["value_json"])
            payload.setdefault("pid", row["pid"])
            payload.setdefault("recorded_at", row["recorded_at"])
            samples.append(payload)
        return samples

    async def append_dtc_codes(
        self,
        codes: Sequence[Tuple[str, str | None]],
        *,
        cleared: bool = False,
    ) -> None:
        """Record a snapshot of diagnostic trouble codes with optional descriptions."""

        if not codes:
            return

        await self._ensure_connection()
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = [(code, description, timestamp, 1 if cleared else 0) for code, description in codes]

        async with self._lock:
            assert self._connection is not None
            await self._connection.executemany(DTC_INSERT, rows)
            await self._connection.commit()

    async def fetch_dtc_history(self, *, limit: int = 100) -> list[DTCRecord]:
        """Retrieve stored diagnostic trouble code events."""

        await self._ensure_connection()
        async with self._lock:
            assert self._connection is not None
            cursor = await self._connection.execute(DTC_HISTORY, (limit,))
            rows = await cursor.fetchall()
            await cursor.close()

        return [
            DTCRecord(
                code=row["code"],
                description=row["description"],
                detected_at=datetime.fromisoformat(row["detected_at"]),
                cleared=bool(row["cleared"]),
            )
            for row in rows
        ]

    async def close(self) -> None:
        """Close the underlying connection."""

        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _ensure_connection(self) -> None:
        if self._connection is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = await aiosqlite.connect(self._db_path)
            self._connection.row_factory = aiosqlite.Row


def _ensure_iso_timestamp(value: Any | None) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, str) and value:
        return value
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
