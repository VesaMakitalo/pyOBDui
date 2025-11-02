"""OBD connection management for pyOBDui."""

from .client import OBDClient, OBDClientError, OBDConnectionError
from .constants import MIN_POLL_INTERVAL

__all__ = ["OBDClient", "OBDClientError", "OBDConnectionError", "MIN_POLL_INTERVAL"]
