"""Configuration utilities for pyOBDui."""

from .constants import CONFIG_SUFFIX, DEFAULT_SUPPORTED_PIDS
from .models import CarConfig
from .service import ConfigDetectionError, ConfigError, ConfigNotFoundError, ConfigService

__all__ = [
    "CarConfig",
    "ConfigService",
    "ConfigError",
    "ConfigNotFoundError",
    "ConfigDetectionError",
    "CONFIG_SUFFIX",
    "DEFAULT_SUPPORTED_PIDS",
]
