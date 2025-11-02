"""Logging helpers."""

from __future__ import annotations

import logging


class _LevelColorFormatter(logging.Formatter):
    """Formatter that colors the level name and omits timestamps."""

    _LEVEL_COLORS = {
        logging.DEBUG: "\033[36m",  # Cyan
        logging.INFO: "\033[32m",  # Green
        logging.WARNING: "\033[33m",  # Yellow
        logging.ERROR: "\033[31m",  # Red
        logging.CRITICAL: "\033[35m",  # Magenta
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        original_levelname = record.levelname
        color_prefix = self._LEVEL_COLORS.get(record.levelno, "")
        if color_prefix:
            record.levelname = f"{color_prefix}{record.levelname}{self._RESET}"
        try:
            return super().format(record)
        finally:
            record.levelname = original_levelname


def configure_logging(level: int = logging.INFO) -> None:
    """Configure application logging with colored level names."""

    handler = logging.StreamHandler()
    handler.setFormatter(_LevelColorFormatter("%(levelname)s %(name)s: %(message)s"))

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
