"""Data models describing vehicle configuration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field  # type: ignore[import]


class CarConfig(BaseModel):
    """Configuration describing how to communicate with a specific vehicle."""

    name: str = Field(..., description="Human-friendly name for the vehicle")
    adapter_port: str = Field(..., description="Serial/Bluetooth port for the adapter")
    database_path: Path = Field(..., description="Path to the vehicle's SQLite database")
    supported_pids: List[str] = Field(
        default_factory=list,
        description="OBD PIDs that are known to work for this vehicle",
    )
    polling_interval: float = Field(
        default=1.0,
        ge=0.1,
        description="Interval in seconds between polling cycles",
    )
    metadata: Dict[str, str] = Field(
        default_factory=dict,
        description="Optional metadata such as make, model, or notes",
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp indicating when the configuration was created",
    )

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        use_enum_values=True,
    )

    def sorted_pids(self) -> List[str]:
        """Return a sorted copy of supported PIDs for convenience."""

        return sorted(self.supported_pids)
