"""PyQt application for visualizing OBD telemetry."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Iterable, Optional

from PyQt6 import QtCore, QtWidgets  # type: ignore[import]

from ..db import DataRepository, DTCRecord
from ..obd_connection import OBDClient
from .constants import DTC_REFRESH_MS, TELEMETRY_REFRESH_MS


class MonitoringApp:
    """High-level wrapper around the PyQt event loop."""

    def __init__(
        self,
        repository: DataRepository,
        obd_client: Optional[OBDClient] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self._repository = repository
        self._obd_client = obd_client
        self._loop = loop or asyncio.get_event_loop()

        self._qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        self._window = MonitoringWindow(repository, obd_client, self._loop)

    def run(self) -> int:
        """Start the Qt event loop."""

        self._window.show()
        return self._qt_app.exec()


class MonitoringWindow(QtWidgets.QMainWindow):
    """Main window displaying telemetry and diagnostics."""

    _logger = logging.getLogger(__name__)

    def __init__(
        self,
        repository: DataRepository,
        obd_client: Optional[OBDClient],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        super().__init__()
        self._repository = repository
        self._obd_client = obd_client
        self._loop = loop

        self.setWindowTitle("pyOBDui - Vehicle Monitor")
        self.resize(960, 600)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)

        layout = QtWidgets.QVBoxLayout(central)

        # Telemetry Table
        self._telemetry_table = QtWidgets.QTableWidget(0, 5, parent=self)
        self._telemetry_table.setHorizontalHeaderLabels(
            ["PID", "Description", "Value", "Unit", "Status"]
        )
        self._telemetry_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(QtWidgets.QLabel("Live Telemetry", self))
        layout.addWidget(self._telemetry_table)

        # Diagnostic Controls
        dtc_layout = QtWidgets.QHBoxLayout()
        self._read_dtcs_button = QtWidgets.QPushButton("Read DTCs", self)
        self._clear_dtcs_button = QtWidgets.QPushButton("Clear DTCs", self)
        self._read_dtcs_button.clicked.connect(self._on_read_dtcs)
        self._clear_dtcs_button.clicked.connect(self._on_clear_dtcs)
        if self._obd_client is None:
            self._read_dtcs_button.setEnabled(False)
            self._clear_dtcs_button.setEnabled(False)
        dtc_layout.addWidget(self._read_dtcs_button)
        dtc_layout.addWidget(self._clear_dtcs_button)
        layout.addLayout(dtc_layout)

        self._dtc_list = QtWidgets.QListWidget(self)
        layout.addWidget(QtWidgets.QLabel("Diagnostic Trouble Codes", self))
        layout.addWidget(self._dtc_list)

        # Status bar message
        self.statusBar().showMessage("Ready")

        self._telemetry_timer = QtCore.QTimer(self)
        self._telemetry_timer.timeout.connect(self._refresh_telemetry)
        self._telemetry_timer.start(TELEMETRY_REFRESH_MS)

        self._dtc_timer = QtCore.QTimer(self)
        self._dtc_timer.timeout.connect(self._refresh_dtc_history)
        self._dtc_timer.start(DTC_REFRESH_MS)

        # Initial fill
        self._refresh_telemetry()
        self._refresh_dtc_history()

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------
    def _refresh_telemetry(self) -> None:
        future = asyncio.run_coroutine_threadsafe(
            self._repository.fetch_latest_samples(), self._loop
        )
        try:
            samples = future.result(timeout=2)
        except Exception as exc:  # pragma: no cover - operational guard
            self._logger.warning("Failed to load telemetry: %s", exc)
            self.statusBar().showMessage("Telemetry refresh failed", 3_000)
            return

        self._populate_telemetry_table(samples)
        self.statusBar().showMessage("Telemetry updated", 1_500)

    def _populate_telemetry_table(self, samples: Iterable[dict[str, Any]]) -> None:
        rows = list(samples)
        self._telemetry_table.setRowCount(len(rows))

        for row_idx, sample in enumerate(rows):
            self._set_table_item(row_idx, 0, sample.get("pid", ""))
            self._set_table_item(row_idx, 1, sample.get("description", ""))

            value = sample.get("display") or sample.get("value", "")
            if isinstance(value, float):
                value_text = f"{value:.2f}"
            else:
                value_text = str(value)
            self._set_table_item(row_idx, 2, value_text)

            self._set_table_item(row_idx, 3, sample.get("unit", ""))
            self._set_table_item(row_idx, 4, sample.get("status", ""))

        self._telemetry_table.resizeColumnsToContents()

    def _set_table_item(self, row: int, column: int, text: str) -> None:
        item = QtWidgets.QTableWidgetItem(text)
        item.setFlags(item.flags() ^ QtCore.Qt.ItemFlag.ItemIsEditable)
        self._telemetry_table.setItem(row, column, item)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def _refresh_dtc_history(self) -> None:
        future = asyncio.run_coroutine_threadsafe(
            self._repository.fetch_dtc_history(limit=100), self._loop
        )
        try:
            history = future.result(timeout=2)
        except Exception as exc:  # pragma: no cover - operational guard
            self._logger.warning("Failed to load DTC history: %s", exc)
            self.statusBar().showMessage("Unable to load DTC history", 3_000)
            return

        self._populate_dtc_list(history)

    def _populate_dtc_list(self, history: Iterable[DTCRecord]) -> None:
        self._dtc_list.clear()
        for record in history:
            status = "Cleared" if record.cleared else "Active"
            timestamp = record.detected_at.strftime("%Y-%m-%d %H:%M:%S")
            description = record.description or ""
            self._dtc_list.addItem(f"[{status}] {record.code} {description} ({timestamp})")

    def _on_read_dtcs(self) -> None:
        if self._obd_client is None:
            return

        future = asyncio.run_coroutine_threadsafe(
            self._obd_client.read_dtcs(persist=True), self._loop
        )
        try:
            codes = future.result(timeout=5)
        except Exception as exc:  # pragma: no cover - hardware dependent failure
            self._logger.error("Failed to read DTCs: %s", exc)
            QtWidgets.QMessageBox.critical(self, "DTC Error", str(exc))
            return

        if not codes:
            QtWidgets.QMessageBox.information(self, "DTCs", "No diagnostic codes reported.")
        else:
            formatted = "\n".join(f"{code} - {desc or 'No description'}" for code, desc in codes)
            QtWidgets.QMessageBox.information(self, "DTCs", formatted)

        self._refresh_dtc_history()

    def _on_clear_dtcs(self) -> None:
        if self._obd_client is None:
            return

        confirm = QtWidgets.QMessageBox.question(
            self,
            "Clear Diagnostic Codes",
            "Are you sure you want to clear stored trouble codes?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )

        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        future = asyncio.run_coroutine_threadsafe(self._obd_client.clear_dtcs(), self._loop)
        try:
            future.result(timeout=5)
        except Exception as exc:  # pragma: no cover - hardware dependent failure
            self._logger.error("Failed to clear DTCs: %s", exc)
            QtWidgets.QMessageBox.critical(self, "DTC Error", str(exc))
            return

        QtWidgets.QMessageBox.information(self, "DTCs", "Clear command sent successfully.")
        self._refresh_dtc_history()
