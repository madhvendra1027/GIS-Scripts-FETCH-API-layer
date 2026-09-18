"""cleaning.py — sits between normalize.py and HazardReadingStore.

Handles missing/nullable fields, out-of-range values, and staleness on the
locked field names normalize.py produces. Nothing here knows about hazard
types or provider names — it only guarantees "the numbers present are
plausible, gaps are filled sensibly, and staleness is flagged."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

STALE_AFTER = timedelta(hours=3)

# Per-field plausible ranges. Extend as new fields/providers are added.
VALID_RANGES: dict[str, tuple[float, float]] = {
    "rainfall_mm_24h": (0.0, 1000.0),
    "rainfall_mm_72h": (0.0, 2000.0),
    "rainfall_intensity_mm_per_hr": (0.0, 300.0),
    "river_level_m": (0.0, 30.0),
    "river_level_change_rate_m_per_hr": (-5.0, 5.0),
    "soil_saturation_pct": (0.0, 100.0),
    "soil_moisture_pct": (0.0, 100.0),
    "elevation_m": (-100.0, 9000.0),
    "distance_to_river_m": (0.0, 50000.0),
    "distance_to_coast_m": (0.0, 50000.0),
    "slope_deg": (0.0, 90.0),
    "vegetation_index": (0.0, 1.0),
    "shoreline_change_rate_m_per_yr": (-50.0, 50.0),
    # NOT a normalized 0-1 index despite the name: gis_fetcher's `marine`
    # provider computes this as (wave_height_m ** 2) * wave_period_s (see
    # its docstring), which lands well above 1.0 for an ordinary sea state
    # (e.g. 1.5m/7s -> 15.75). The old (0.0, 1.0) bound meant almost every
    # real reading was silently dropped as "out of range" and replaced by
    # an imputed fallback. Upper bound here is generous (H up to ~5m,
    # T up to ~20s -> 500) to cover a genuine storm without being so wide
    # it stops catching real garbage/sentinel values from the API.
    "wave_energy_index": (0.0, 500.0),
    "mangrove_cover_pct": (0.0, 100.0),
    "humidity_pct": (0.0, 100.0),
    "temperature_c": (-20.0, 60.0),
    "wind_speed_kmph": (0.0, 400.0),
    "flood_status_severity_code": (0.0, 3.0),
}

# Coarse fallback used only when there is no last-known zone value either.
# Placeholders — replace with real regional averages once available.
REGIONAL_DEFAULTS: dict[str, float] = {
    "rainfall_mm_24h": 0.0,
    "rainfall_mm_72h": 0.0,
    "soil_saturation_pct": 40.0,
    "soil_moisture_pct": 40.0,
    "humidity_pct": 60.0,
    "temperature_c": 27.0,
    "wind_speed_kmph": 10.0,
    "vegetation_index": 0.4,
    # Matches the corrected VALID_RANGES scale above, not a 0-1 index:
    # roughly a 1.4m/10s sea state ((1.4**2)*10 ~= 19.6), a typical calm-
    # to-moderate day rather than a storm. The old 0.3 default was
    # calibrated for the now-fixed (0.0, 1.0) bound bug and, once that
    # bound moved to the real (0.0, 500.0) scale, silently represented an
    # almost-flat-calm sea every time it was used as a fallback.
    "wave_energy_index": 20.0,
    "mangrove_cover_pct": 0.0,
}


@dataclass
class CleaningResult:
    """A cleaned parameter dict plus a record of what happened to it, so
    downstream stages (and the data_quality flag on HazardReading) know how
    much to trust each value.
    """

    parameters: dict[str, Any]
    imputed_fields: list[str] = field(default_factory=list)
    dropped_fields: list[str] = field(default_factory=list)
    is_stale: bool = False


class ZoneHistory:
    """In-memory 'last known good value' cache per zone, per field.

    Swap for a real store (Redis/DB) in production — the interface
    (`get_last_known`, `update`) is what matters, not the backing store.
    """

    def __init__(self) -> None:
        self._last_known: dict[tuple[str, str], float] = {}

    def get_last_known(self, zone_id: str, field_name: str) -> Optional[float]:
        return self._last_known.get((zone_id, field_name))

    def update(self, zone_id: str, field_name: str, value: float) -> None:
        self._last_known[(zone_id, field_name)] = value


def clean_reading(
    zone_id: str,
    raw_parameters: dict[str, Any],
    recorded_at: datetime,
    history: ZoneHistory,
) -> CleaningResult:
    """Validate, impute, and flag staleness for one normalized parameter
    dict. Field names must already be the locked names normalize.py
    produces — this function does not rename anything.
    """
    cleaned: dict[str, Any] = {}
    imputed: list[str] = []
    dropped: list[str] = []

    for field_name, value in raw_parameters.items():
        if value is None:
            fallback = history.get_last_known(zone_id, field_name)
            if fallback is None:
                fallback = REGIONAL_DEFAULTS.get(field_name)
            if fallback is None:
                # No sensible fallback exists (e.g. a genuinely optional
                # field like river_level_m with no nearby gauge) — leave it
                # null; feature_engineering.py / predictor.py decide how to
                # treat a missing value.
                cleaned[field_name] = None
                continue
            cleaned[field_name] = fallback
            imputed.append(field_name)
            continue

        bounds = VALID_RANGES.get(field_name)
        if bounds is not None:
            low, high = bounds
            if not (low <= value <= high):
                dropped.append(field_name)
                fallback = history.get_last_known(zone_id, field_name)
                cleaned[field_name] = fallback  # may still be None
                if fallback is not None:
                    imputed.append(field_name)
                continue

        cleaned[field_name] = value
        if isinstance(value, (int, float)):
            history.update(zone_id, field_name, float(value))

    is_stale = (datetime.now(timezone.utc) - recorded_at) > STALE_AFTER

    return CleaningResult(
        parameters=cleaned,
        imputed_fields=imputed,
        dropped_fields=dropped,
        is_stale=is_stale,
    )
