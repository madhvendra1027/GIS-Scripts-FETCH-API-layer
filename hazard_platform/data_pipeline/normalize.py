"""normalize.py — maps raw gis_fetcher Features into the locked
HazardReading.parameters field names required by ml_service.

Runs BEFORE cleaning.py: cleaning.py's validation ranges are keyed on the
locked field names produced here, so renaming has to happen first. The
actual order is:

    gis_fetcher  ->  normalize.py  ->  cleaning.py  ->  HazardReadingStore

Restricted to the four hazards named in the problem statement: FLOOD,
LANDSLIDE, EROSION, CLOUDBURST. Fields with no current data source are left
explicitly as None rather than guessed — see README's provider/field gap
table for what still needs a new gis_fetcher provider or manual dataset.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol


class FeatureLike(Protocol):
    """Structural shape of gis_fetcher.core.base.Feature — duck-typed here
    so this module doesn't hard-depend on the gis_fetcher package.
    """

    properties: dict[str, Any]
    source: str


def _get(feature: FeatureLike, *keys: str) -> Optional[Any]:
    for key in keys:
        value = feature.properties.get(key)
        if value is not None:
            return value
    return None


def weather_to_flood_fields(weather: FeatureLike) -> dict[str, Any]:
    return {
        "rainfall_mm_24h": _get(weather, "precipitation_24h", "rain_24h"),
        "rainfall_mm_72h": _get(weather, "precipitation_72h", "rain_72h"),
        # river_level_change_rate_m_per_hr / soil_saturation_pct /
        # historical_flood_count come from river_discharge_to_flood_fields(),
        # land_hydrology_to_flood_fields(), and historical_to_fields() below —
        # merge_shared_fields() combines them with this dict.
        #
        # river_level_m and distance_to_river_m are placeholders here for
        # the same reason sediment_type_code/mangrove_cover_pct are None
        # in osm_to_erosion_fields() below: they're StaticDatasetStore
        # fields now (fetch_river_level.py / fetch_river_distance.py,
        # orchestrated by auto_refresh.py), not something any per-run
        # gis_fetcher provider returns. pipeline_runner.py's
        # merge_shared_fields(*field_dicts, static_fields) call is what
        # actually fills them in — static_fields only carries non-None
        # entries, so a real ingested value here always overrides this
        # None, and a genuinely never-ingested field just stays None.
        "river_level_m": None,
        "river_level_change_rate_m_per_hr": None,
        "soil_saturation_pct": None,
        "distance_to_river_m": None,
        "historical_flood_count": None,
        # flood_status_severity_code: same StaticDatasetStore-fed
        # placeholder pattern as river_level_m/distance_to_river_m
        # above — fetch_flood_status.py (auto_refresh.py) fills it in,
        # gated on the same GOOGLE_FLOOD_API_KEY as river_level_m.
        "flood_status_severity_code": None,
    }


def weather_to_landslide_fields(weather: FeatureLike) -> dict[str, Any]:
    """LANDSLIDE's only weather-derived field is rainfall_mm_72h (heavy
    rain saturating a slope is the trigger predictor.py weights). Added
    because HAZARD_PROVIDERS["LANDSLIDE"] already calls the `weather`
    provider, but nothing previously read its output for this hazard --
    the rainfall it fetched was silently discarded.
    """
    return {"rainfall_mm_72h": _get(weather, "precipitation_72h", "rain_72h")}


def river_discharge_to_flood_fields(discharge: FeatureLike) -> dict[str, Any]:
    """From the `river_discharge` provider (Open-Meteo/GloFAS).

    river_level_m stays None here on purpose: GloFAS gives discharge
    (m3/s), not gauge stage (m) — there's no free/cheap live gauge-level
    API covering India (see providers/river_discharge.py's docstring for
    why India-WRIS/CWC don't qualify). The day-over-day change rate is
    still a genuinely useful "rising vs falling" signal on its own.
    """
    return {
        "river_level_change_rate_m_per_hr": _get(discharge, "river_level_change_rate_m_per_hr"),
        "river_discharge_m3s": _get(discharge, "river_discharge_m3s"),
    }


def land_hydrology_to_flood_fields(hydro: FeatureLike) -> dict[str, Any]:
    """From the `land_hydrology` provider (NASA POWER root-zone wetness)."""
    return {"soil_saturation_pct": _get(hydro, "soil_saturation_pct")}


def weather_to_cloudburst_fields(weather: FeatureLike) -> dict[str, Any]:
    return {
        "rainfall_intensity_mm_per_hr": _get(weather, "precipitation_intensity", "rain_rate"),
        "humidity_pct": _get(weather, "relative_humidity_2m", "humidity"),
        "temperature_c": _get(weather, "temperature_2m", "temperature"),
        "wind_speed_kmph": _get(weather, "wind_speed_10m", "windspeed"),
        # elevation_m is filled from the elevation provider, not here —
        # see merge_shared_fields() below.
        "elevation_m": None,
        "historical_cloudburst_count": None,
    }


def elevation_to_shared_fields(elevation: FeatureLike) -> dict[str, Any]:
    """elevation_m is shared across FLOOD, LANDSLIDE and CLOUDBURST — fetched
    once and merged into each hazard's dict rather than re-fetched per
    hazard (mirrors ml.md's note on this exact duplication).

    Reads the "elevation_m" property key -- both the retired Open-Elevation
    provider and the current Open-Meteo one write their value under that
    exact key (see gis_fetcher/providers/elevation.py), never a bare
    "elevation". The previous `_get(elevation, "elevation")` here silently
    missed on every call regardless of which provider was live underneath
    it, which is why elevation_m showed up as None even on runs where the
    `elevation` provider itself reported 'ok' (2026-09-18 pipeline_runner
    output for Z-ODISHA-PURI-01 is the reproduction case that surfaced
    this).
    """
    return {"elevation_m": _get(elevation, "elevation_m")}


def slope_to_landslide_fields(slope: FeatureLike) -> dict[str, Any]:
    """From the `slope` provider — a finite-difference slope derived from
    5 elevation samples, not a single OSM tag. See providers/slope.py for
    method + accuracy caveat (coarse, not DEM-raster-grade).
    """
    return {"slope_deg": _get(slope, "slope_deg")}


def soil_to_landslide_fields(soil: FeatureLike) -> dict[str, Any]:
    """From the `soil` provider (ISRIC SoilGrids texture classification)."""
    return {"soil_type_code": _get(soil, "soil_type_code")}


def land_hydrology_to_landslide_fields(hydro: FeatureLike) -> dict[str, Any]:
    """From the `land_hydrology` provider (NASA POWER surface wetness,
    used as a soil_moisture_pct proxy)."""
    return {"soil_moisture_pct": _get(hydro, "soil_moisture_pct")}


def vegetation_to_landslide_fields(veg: FeatureLike) -> dict[str, Any]:
    """From the `vegetation` provider (Agromonitoring NDVI). Requires a
    free API key (see providers/vegetation.py) — stays None if unset,
    same behavior as before this provider existed.
    """
    return {"vegetation_index": _get(veg, "vegetation_index")}


def osm_to_landslide_fields(osm_features: list[FeatureLike]) -> dict[str, Any]:
    # OSM has none of slope/soil/vegetation — those now come from the
    # slope/soil/vegetation providers above. Kept only so existing
    # callers passing OSM features here don't break.
    return {}


def osm_to_erosion_fields(osm_features: list[FeatureLike]) -> dict[str, Any]:
    distance_to_coast_m = None
    for f in osm_features:
        if f.properties.get("natural") == "coastline":
            distance_to_coast_m = f.properties.get("distance_m")
            break
    return {
        # shoreline_change_rate_m_per_yr, sediment_type_code and
        # mangrove_cover_pct have no free/cheap LIVE API (confirmed by
        # search — ISRO/NCCR's shoreline-change atlas and Global Mangrove
        # Watch are downloadable static datasets, not REST endpoints).
        # Left None from a live fetch on purpose — see
        # STATIC_DATASETS.md for the one-time ingestion path instead of
        # pretending a live provider exists for these.
        "shoreline_change_rate_m_per_yr": None,
        "distance_to_coast_m": distance_to_coast_m,
        "sediment_type_code": None,
        "mangrove_cover_pct": None,
    }


def marine_to_erosion_fields(marine: FeatureLike) -> dict[str, Any]:
    """From the `marine` provider (Open-Meteo wave/marine API)."""
    return {"wave_energy_index": _get(marine, "wave_energy_index")}


def historical_to_fields(history: FeatureLike, hazard_type: str) -> dict[str, Any]:
    """From the `historical_events` provider (ReliefWeb). Country-level
    granularity, not per-zone — see providers/historical_events.py's
    caveat before trusting this as a precise local count.
    """
    field_name = {
        "FLOOD": "historical_flood_count",
        "LANDSLIDE": "historical_landslide_count",
        "EROSION": "historical_erosion_events",
        "CLOUDBURST": "historical_cloudburst_count",
    }[hazard_type]
    return {field_name: _get(history, field_name)}


def merge_shared_fields(*field_dicts: dict[str, Any]) -> dict[str, Any]:
    """Later dicts overwrite earlier ones for shared keys (e.g. elevation_m
    coming from the elevation provider overwriting a hazard dict's
    placeholder None)."""
    merged: dict[str, Any] = {}
    for d in field_dicts:
        for key, value in d.items():
            if value is not None or key not in merged:
                merged[key] = value
    return merged
