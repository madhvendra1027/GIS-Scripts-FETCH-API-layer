"""feature_engineering.py — turns a HazardReading.parameters dict into the
ordered feature set each hazard's scorer expects.

Field order is documented per hazard so predictor.py and any future trained
model agree on layout. This is also the one place that would later add
derived math (rolling rainfall averages, rate-of-change across multiple
readings) without touching predictor.py's scoring logic.
"""
from __future__ import annotations

from typing import Any

from data_pipeline.models import HazardType

FIELD_ORDER: dict[HazardType, list[str]] = {
    HazardType.FLOOD: [
        "rainfall_mm_24h", "rainfall_mm_72h", "river_level_m",
        "river_level_change_rate_m_per_hr", "soil_saturation_pct",
        "elevation_m", "distance_to_river_m", "historical_flood_count",
    ],
    HazardType.LANDSLIDE: [
        "slope_deg", "rainfall_mm_72h", "soil_moisture_pct",
        "soil_type_code", "vegetation_index", "historical_landslide_count",
    ],
    HazardType.EROSION: [
        "shoreline_change_rate_m_per_yr", "wave_energy_index",
        "distance_to_coast_m", "sediment_type_code", "mangrove_cover_pct",
        "historical_erosion_events",
    ],
    HazardType.CLOUDBURST: [
        "rainfall_intensity_mm_per_hr", "humidity_pct", "temperature_c",
        "elevation_m", "wind_speed_kmph", "historical_cloudburst_count",
    ],
}


def build_feature_dict(hazard_type: HazardType, parameters: dict[str, Any]) -> dict[str, Any]:
    """Return only the fields relevant to this hazard, missing fields
    explicit as None — predictor.py decides how to treat a gap.
    """
    fields = FIELD_ORDER[hazard_type]
    return {f: parameters.get(f) for f in fields}


def build_feature_vector(hazard_type: HazardType, parameters: dict[str, Any]) -> list[Any]:
    """Same as build_feature_dict but as an ordered list — the shape a
    trained model would actually receive. None values here mean a real
    model would need imputation upstream (cleaning.py) before use.
    """
    fields = FIELD_ORDER[hazard_type]
    return [parameters.get(f) for f in fields]
