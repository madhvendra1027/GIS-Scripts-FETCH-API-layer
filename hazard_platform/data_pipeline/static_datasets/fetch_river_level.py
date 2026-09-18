"""fetch_river_level.py — automated ingestion of river_level_m
(FLOOD, absolute gauge stage) via Google's Flood Forecasting API
(floodforecasting.googleapis.com), found by searching specifically for
a live source for this field after STATIC_DATASETS.md had marked it as
having no live API at all.

WHY THIS IS DIFFERENT FROM EVERY OTHER FETCH_*.PY IN THIS PACKAGE:
every other automated fetcher (sediment type, historical counts, river
distance) is free and requires no key or signup -- you just run it.
This one is also free (CC BY 4.0, no charge), but Google gates the API
behind an approval step: you join a waitlist, get an approval email,
then enable the API on a Google Cloud project and generate an API key.
See "PAPERWORK STEPS" below for the exact process. Until you've done
that and set the `GOOGLE_FLOOD_API_KEY` environment variable,
`auto_refresh.py` skips this fetcher entirely (see its
`_active_auto_fields()`, which calls this file's `is_configured()`)
rather than silently failing on every run -- river_level_m simply stays
the documented gap it always was until you complete the signup. No
separate command is ever needed either way: once the key is set,
`pipeline_runner.py` (via `auto_refresh.py`) picks this fetcher up on
its very next run for any zone whose river_level_m is missing/stale --
same zero-touch path as every other field in this package.

WHY THIS SOURCE, CONCRETELY: Google's Flood Hub / Flood Forecasting API
publishes real-time riverine forecasts for gauges in ~150 countries,
including India, and — critically for this field — India's gauges
report water-level (meters), not just discharge (m3/s); see
`GaugeModel.gaugeValueUnit`. That is exactly this project's
`river_level_m` unit, unlike GloFAS discharge (already used for
`river_level_change_rate_m_per_hr`).

PAPERWORK STEPS (do this once, outside this repo, before this fetcher
does anything):
    1. Fill out the waitlist form: see the URL in this repo's
       STATIC_DATASETS.md / README.md ("Getting river_level_m live").
    2. Wait for Google's approval email.
    3. Reply to that email with your Google Cloud Project ID (create a
       project first at https://cloud.google.com/resource-manager/docs/creating-managing-projects
       if you don't have one).
    4. Create an API key for that project (see
       https://support.google.com/googleapi/answer/6158862), or reuse
       an existing Google Cloud API key.
    5. Enable the Flood Forecasting API for that project at
       https://console.cloud.google.com/apis/library/floodforecasting.googleapis.com
       -- this only works once step 3's reply has been processed.
    6. Set the environment variable before running anything in this
       repo: `export GOOGLE_FLOOD_API_KEY=your_key_here`.

HONEST LIMITATION: the exact JSON field names below are built from
Google's published REST reference (gauges.searchGaugesByArea,
gauges.queryGaugeForecasts, gaugeModels.batchGet) rather than a live
tested response, since this API requires the approval above before any
call can be made. Every parsing step is wrapped defensively -- an
unexpected schema, like an unreachable endpoint, degrades to "no
override this run", never a crash. Treat this script as needing one
live smoke-test against your own approved key before fully trusting it
-- see the module-level `main()` for a one-zone CLI check for exactly
that purpose.

THEN RUN (usually you don't call this directly -- pipeline_runner.py
does, via auto_refresh.py, only once GOOGLE_FLOOD_API_KEY is set):

    export GOOGLE_FLOOD_API_KEY=...
    python3 -m data_pipeline.static_datasets.fetch_river_level --zone-ids Z-ODISHA-PURI-01
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Optional

from .store import StaticDatasetStore

API_BASE = "https://floodforecasting.googleapis.com/v1"
FIELD_NAME = "river_level_m"
ENV_VAR = "GOOGLE_FLOOD_API_KEY"

# Degrees to pad a zone's bbox by when asking Google for nearby gauges --
# gauges are sparse, so this is wider than fetch_river_distance.py's
# search radius on purpose.
_GAUGE_SEARCH_PAD_DEG = 0.5


def is_configured() -> bool:
    """auto_refresh.py checks this before adding river_level_m to its
    auto-fetched field list at all -- see that module's docstring on
    why an unconfigured key means 'skip', not 'retry forever'."""
    return bool(os.environ.get(ENV_VAR))


def _load_zones(zone_ids: Optional[list]):
    from zones import list_zones

    registry = {z.zone_id: z for z in list_zones()}
    if zone_ids is None:
        return list(registry.values())
    unknown = [zid for zid in zone_ids if zid not in registry]
    if unknown:
        raise ValueError(f"Unknown zone_id(s): {unknown}. Known zones: {sorted(registry)}")
    return [registry[zid] for zid in zone_ids]


async def _search_gauges_near_zone(session, api_key: str, zone) -> list:
    """POST gauges:searchGaugesByArea, restricted to India, for a bbox
    padded around the zone. Returns [] on any failure -- a bad key, a
    schema change, a network error should all degrade the same way."""
    body = {
        "regionCode": "IN",
        "includeNonQualityVerified": True,
    }
    try:
        async with session.post(
            f"{API_BASE}/gauges:searchGaugesByArea",
            params={"key": api_key},
            json=body,
            timeout=20,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    except Exception:  # noqa: BLE001
        return []

    gauges = data.get("gauges", [])
    lon = (zone.min_lon + zone.max_lon) / 2
    lat = (zone.min_lat + zone.max_lat) / 2
    nearby = []
    for gauge in gauges:
        location = gauge.get("location") or {}
        g_lat, g_lon = location.get("latitude"), location.get("longitude")
        if g_lat is None or g_lon is None:
            continue
        if (
            zone.min_lat - _GAUGE_SEARCH_PAD_DEG <= g_lat <= zone.max_lat + _GAUGE_SEARCH_PAD_DEG
            and zone.min_lon - _GAUGE_SEARCH_PAD_DEG <= g_lon <= zone.max_lon + _GAUGE_SEARCH_PAD_DEG
        ):
            nearby.append(gauge)
    nearby.sort(key=lambda g: abs(g["location"]["latitude"] - lat) + abs(g["location"]["longitude"] - lon))
    return nearby


async def _gauge_reports_water_level(session, api_key: str, gauge_id: str) -> bool:
    """Checks gaugeModels:batchGet for this gauge's valueUnit. Only a
    METERS/water-level gauge is usable for river_level_m -- a discharge
    (m3/s) gauge is the wrong unit entirely and must not be silently
    treated as meters."""
    try:
        async with session.get(
            f"{API_BASE}/gaugeModels:batchGet",
            params={"key": api_key, "names": [f"gaugeModels/{gauge_id}"]},
            timeout=20,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    except Exception:  # noqa: BLE001
        return False
    models = data.get("gaugeModels", [])
    if not models:
        return False
    unit = str(models[0].get("gaugeValueUnit", ""))
    return "METER" in unit.upper()


async def _latest_forecast_value(session, api_key: str, gauge_id: str) -> Optional[float]:
    """GET gauges:queryGaugeForecasts for one gauge, returns the
    nearest-in-time forecast value (the closest thing to 'current
    level' this forecast-oriented API exposes -- there is no separate
    'observed now' endpoint documented)."""
    try:
        async with session.get(
            f"{API_BASE}/gauges:queryGaugeForecasts",
            params={"key": api_key, "gaugeIds": [gauge_id]},
            timeout=20,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    except Exception:  # noqa: BLE001
        return None

    forecast_sets = data.get("forecasts", {})
    forecast_set = forecast_sets.get(gauge_id)
    if not forecast_set:
        return None
    forecasts = forecast_set.get("forecasts", [])
    if not forecasts:
        return None
    first = forecasts[0]
    timed_values = first.get("forecastTimedValues") or first.get("timedValues") or []
    if not timed_values:
        return None
    value = timed_values[0].get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _fetch_one_zone(session, api_key: str, zone) -> Optional[float]:
    gauges = await _search_gauges_near_zone(session, api_key, zone)
    for gauge in gauges:
        gauge_id = gauge.get("gaugeId")
        if not gauge_id:
            continue
        if not await _gauge_reports_water_level(session, api_key, gauge_id):
            continue
        value = await _latest_forecast_value(session, api_key, gauge_id)
        if value is not None:
            return value
    return None


async def _gather(zones: list, api_key: str) -> dict:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        results = {}
        for zone in zones:
            results[zone.zone_id] = await _fetch_one_zone(session, api_key, zone)
        return results


def fetch_river_levels_for_zones(zone_ids: Optional[list] = None) -> dict:
    """Returns {zone_id: meters_or_None}. Returns all-None immediately,
    with no network calls, if GOOGLE_FLOOD_API_KEY isn't set -- see
    `is_configured()`."""
    zones = _load_zones(zone_ids)
    api_key = os.environ.get(ENV_VAR)
    if not api_key:
        return {zone.zone_id: None for zone in zones}
    return asyncio.run(_gather(zones, api_key))


def ingest_river_levels(zone_ids: Optional[list] = None, store: Optional[StaticDatasetStore] = None) -> dict:
    """auto_refresh.py's entry point."""
    store = store or StaticDatasetStore()
    results = fetch_river_levels_for_zones(zone_ids)
    for zone_id, value in results.items():
        if value is None:
            continue
        store.upsert(zone_id, FIELD_NAME, float(value), "GoogleFloodForecastingAPI")
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zone-ids", nargs="*", default=None, help="Restrict to these zone_id(s); default: every zone in zones.py")
    parser.add_argument("--db", default="static_zone_data.db")
    args = parser.parse_args(argv)

    if not is_configured():
        print(
            f"{ENV_VAR} is not set -- river_level_m has no source configured. "
            "See this file's module docstring for the one-time signup steps.",
            file=sys.stderr,
        )
        return 1

    store = StaticDatasetStore(db_path=args.db)
    results = ingest_river_levels(args.zone_ids, store)
    for zone_id, value in results.items():
        if value is None:
            print(f"{zone_id}: no water-level gauge found nearby -- not overridden")
        else:
            print(f"{zone_id}: river_level_m={value:.2f} (Google Flood Forecasting API)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
