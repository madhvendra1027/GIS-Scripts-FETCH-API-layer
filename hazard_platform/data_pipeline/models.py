"""Shared data models for the hazard data pipeline.

Restricted to the four hazards named in the problem statement — landslides,
floods, coastal erosion, and cloudbursts. No seismic/earthquake hazard type
is defined here, even though gis_fetcher can fetch earthquake data as a
bonus source; it isn't one of the hazards this platform is scoped to score.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class HazardType(str, Enum):
    FLOOD = "FLOOD"
    LANDSLIDE = "LANDSLIDE"
    EROSION = "EROSION"
    CLOUDBURST = "CLOUDBURST"


class DataQuality(str, Enum):
    RAW = "raw"
    IMPUTED = "imputed"
    STALE = "stale"


@dataclass
class HazardReading:
    """One normalized, hazard-specific reading for a single zone.

    `parameters` follows the locked field-name contract for its
    `hazard_type` — see ml_service/features/feature_engineering.py for the
    exact field list expected per hazard. Produced by normalize.py, then
    passed through cleaning.py, before being written here.
    """

    zone_id: str
    hazard_type: HazardType
    source: str
    recorded_at: datetime
    parameters: dict[str, Any]
    data_quality: DataQuality = DataQuality.RAW

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "hazard_type": self.hazard_type.value,
            "source": self.source,
            "recorded_at": self.recorded_at.isoformat(),
            "parameters": self.parameters,
            "data_quality": self.data_quality.value,
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
