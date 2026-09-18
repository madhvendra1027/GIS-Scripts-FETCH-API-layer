"""fetch_sediment_type.py — fully automated ingestion of
sediment_type_code (EROSION), replacing the manual GSI-Bhukosh CSV path
that used to be the only option (see load_sediment_type.py, kept below
as an optional manual override for whenever someone actually has a real
GSI coastal-geomorphology classification for a zone).

WHY THIS EXISTS: load_sediment_type.py's docstring was honest that GSI's
Bhukosh coastal-geomorphology maps are usually a PDF report or a static
map image, not a shapefile with a numeric column -- so there is no
national, downloadable, joinable layer for `spatial_join.py` to run
against. That's still true. What closes the automation gap is the
fallback load_sediment_type.py already named as the practical route for
a demo: SoilGrids' texture classification at the coastal zone, reusing
the identical USDA texture-code convention so EROSION and LANDSLIDE stay
on one numbering scheme.

CHANGED 2026-09-18: this used to query ISRIC's own REST API
(rest.isric.org) directly, free and keyless. ISRIC has since paused that
REST API indefinitely (their own docs: "we are currently experiencing
issues... and have decided to temporarily pause the service", no ETA)
-- and it was always documented as a best-effort beta layer, not a
production dependency. This now queries the same underlying SoilGrids
data via Google Earth Engine instead, matching the switch just made in
gis_fetcher/providers/soil.py (LANDSLIDE's soil_type_code) for the same
reason. IMPORTANT CONSEQUENCE: this field is NO LONGER always-on/no-key
the way it used to be -- it now needs the one-time GEE setup described
below, same as river_level_m needs GOOGLE_FLOOD_API_KEY. See
`is_configured()`; auto_refresh.py only includes this field in its
auto-fetch list once that's true, exactly like it already does for
river_level_m.

This script still deliberately has no gis_fetcher import (this package
stays dependency-free of gis_fetcher, same as fetch_historical_events.py)
-- the GEE asset ids, band names, and texture classifier below are a
literal copy of gis_fetcher/providers/soil.py's, not an import.

ONE-TIME GEE SETUP (do this once, outside this repo):
    1. Create a Google Cloud project and enable the Earth Engine API.
    2. Register the project for Earth Engine (noncommercial/Community
       tier is fine -- no billing account required for that tier).
    3. Create a service account, grant it the "Earth Engine Resource
       Viewer" IAM role, and generate+download a JSON key for it.
    4. Set two environment variables (e.g. in .env, see .env.example):
           GEE_SERVICE_ACCOUNT_EMAIL=your-sa@your-project.iam.gserviceaccount.com
           GEE_SERVICE_ACCOUNT_KEY_PATH=secrets/your-key-file.json
    5. pip install earthengine-api (already in requirements.txt).
See SESSIONS_README.md for the full click-by-click walkthrough.

HONEST LIMITATION, stated plainly: this is a proxy, not real GSI
classification. Soil texture at a zone's centroid correlates reasonably
with coastal sediment character (sandy vs. muddy/clayey) but is not a
substitute for GSI's actual coastal geomorphology mapping (rocky
headland vs. sandy beach vs. tidal mudflat can diverge from what a
250m soil grid cell reports). The `source` string this writes always
ends in "_proxy_auto" so nobody downstream mistakes this for verified
GSI data. If a real GSI shapefile/lookup ever becomes available for a
zone, run load_sediment_type.py with it -- pipeline_runner.py's merge
order means a manually-ingested non-"_proxy_auto" value should be
treated as the more authoritative one operationally, even though both
currently live in the same store column (see auto_refresh.py's
docstring for exactly how staleness/override interacts with this).

THEN RUN (usually you don't call this directly -- pipeline_runner.py
does, via auto_refresh.py, whenever a zone's sediment_type_code is
missing/stale AND GEE is configured):

    python3 -m data_pipeline.static_datasets.fetch_sediment_type
    python3 -m data_pipeline.static_datasets.fetch_sediment_type --zone-ids Z-ODISHA-PURI-01
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
from typing import Optional

from .store import StaticDatasetStore

FIELD_NAME = "sediment_type_code"

# Same env-var pair as gis_fetcher/providers/soil.py, read directly here
# (not via providers.yaml/config.py) since this package has no
# gis_fetcher dependency and doesn't use that config loader -- matches
# fetch_river_level.py's ENV_VAR/is_configured() pattern for
# GOOGLE_FLOOD_API_KEY.
ENV_SERVICE_ACCOUNT = "GEE_SERVICE_ACCOUNT_EMAIL"
ENV_KEY_PATH = "GEE_SERVICE_ACCOUNT_KEY_PATH"


def is_configured() -> bool:
    """True once both GEE env vars are set. Checked fresh (not cached)
    so setting them mid-session and re-running picks it up immediately,
    same as fetch_river_level.py's is_configured()."""
    return bool(os.environ.get(ENV_SERVICE_ACCOUNT)) and bool(os.environ.get(ENV_KEY_PATH))


# Literal copy of gis_fetcher/providers/soil.py's asset/band table --
# see that file's module docstring for why this isn't an import.
_GEE_ASSET_BAND = {
    "sand": ("projects/soilgrids-isric/sand_mean", "sand_0-5cm_mean"),
    "silt": ("projects/soilgrids-isric/silt_mean", "silt_0-5cm_mean"),
    "clay": ("projects/soilgrids-isric/clay_mean", "clay_0-5cm_mean"),
}
_D_FACTOR = 10

_ee_lock = threading.Lock()
_ee_ready = False


def _ensure_ee_initialized() -> None:
    global _ee_ready
    if _ee_ready:
        return
    with _ee_lock:
        if _ee_ready:
            return
        import ee

        service_account = os.environ[ENV_SERVICE_ACCOUNT]
        key_path = os.environ[ENV_KEY_PATH]
        credentials = ee.ServiceAccountCredentials(service_account, key_path)
        ee.Initialize(credentials)
        _ee_ready = True


def _query_sand_silt_clay_pct(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> dict:
    """Blocking GEE call -- run via asyncio.to_thread from
    _fetch_one_zone below, same reasoning as gis_fetcher/providers/soil.py."""
    _ensure_ee_initialized()
    import ee

    region = ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])
    image = None
    for prop, (asset_id, band) in _GEE_ASSET_BAND.items():
        band_image = ee.Image(asset_id).select(band).rename(prop)
        image = band_image if image is None else image.addBands(band_image)

    stats = image.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=250, bestEffort=True
    ).getInfo()

    fractions = {}
    for prop in _GEE_ASSET_BAND:
        raw = stats.get(prop)
        if raw is not None:
            fractions[prop] = raw / _D_FACTOR
    return fractions


# GEE is Google-run infrastructure, far less flaky than ISRIC's own beta
# REST API was -- but a retry loop costs nothing and this is still a
# network call, so kept as cheap insurance, same shape as before.
_MAX_RETRIES = 2

# Identical convention to gis_fetcher/providers/soil.py's _TEXTURE_ORDER
# (1=clay ... 12=sand), repeated here rather than imported so this
# package has zero hard dependency on gis_fetcher -- same pattern
# load_sediment_type.py's SEDIMENT_CODE_HINTS already used.
_TEXTURE_ORDER = [
    (1, "clay"), (2, "silty_clay"), (3, "sandy_clay"), (4, "clay_loam"),
    (5, "silty_clay_loam"), (6, "sandy_clay_loam"), (7, "loam"),
    (8, "silty_loam"), (9, "sandy_loam"), (10, "silt"),
    (11, "loamy_sand"), (12, "sand"),
]


def _classify_usda_texture(sand_pct: float, silt_pct: float, clay_pct: float) -> tuple[int, str]:
    """Same simplified USDA texture classification as soil.py -- kept as
    a literal copy (not an import) for the same no-gis_fetcher-dependency
    reason as the code table above."""
    if clay_pct >= 40:
        code, name = (1, "clay") if silt_pct < 40 else (2, "silty_clay")
        if sand_pct >= 45 and clay_pct < 55:
            code, name = (3, "sandy_clay")
        return code, name
    if clay_pct >= 27:
        if sand_pct >= 45:
            return 6, "sandy_clay_loam"
        if silt_pct >= 40:
            return 5, "silty_clay_loam"
        return 4, "clay_loam"
    if silt_pct >= 80:
        return 10, "silt"
    if silt_pct >= 50:
        return 8, "silty_loam"
    if sand_pct >= 85:
        return 12, "sand"
    if sand_pct >= 70:
        return 11, "loamy_sand"
    if sand_pct >= 43 and clay_pct < 20:
        return 9, "sandy_loam"
    return 7, "loam"


def _load_zones(zone_ids: Optional[list]):
    from zones import list_zones

    registry = {z.zone_id: z for z in list_zones()}
    if zone_ids is None:
        return list(registry.values())
    unknown = [zid for zid in zone_ids if zid not in registry]
    if unknown:
        raise ValueError(f"Unknown zone_id(s): {unknown}. Known zones: {sorted(registry)}")
    return [registry[zid] for zid in zone_ids]


async def _fetch_one_zone(zone) -> Optional[tuple[int, str]]:
    """One GEE query averaged over the zone's own bbox, retried up to
    `_MAX_RETRIES` times with capped exponential backoff. Returns None
    (not an exception) if every attempt fails, or on a genuine
    no-coverage response -- e.g. open ocean cells genuinely have no
    SoilGrids value -- so a flaky call degrades to "no override this
    run" rather than crashing the whole batch."""
    fractions = None
    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            fractions = await asyncio.to_thread(
                _query_sand_silt_clay_pct, zone.min_lon, zone.min_lat, zone.max_lon, zone.max_lat
            )
            break
        except Exception as exc:  # noqa: BLE001 - network/auth errors -> retry, then no override
            last_error = exc
            backoff = min(2 ** attempt, 15)
            if attempt < _MAX_RETRIES:
                print(f"fetch_sediment_type: {zone.zone_id} attempt {attempt}/{_MAX_RETRIES} failed: {type(exc).__name__}: {exc} (retrying in {backoff}s)")
                await asyncio.sleep(backoff)
    if fractions is None:
        print(f"fetch_sediment_type: {zone.zone_id} failed after {_MAX_RETRIES} attempts: {type(last_error).__name__}: {last_error}")
        return None

    if not {"sand", "silt", "clay"} <= fractions.keys():
        print(f"fetch_sediment_type: {zone.zone_id} got a response but no usable sand/silt/clay -- found: {sorted(fractions.keys()) or 'none'}")
        return None
    return _classify_usda_texture(fractions["sand"], fractions["silt"], fractions["clay"])


async def _gather(zones: list) -> dict:
    results = await asyncio.gather(*[_fetch_one_zone(zone) for zone in zones])
    return {zone.zone_id: result for zone, result in zip(zones, results)}


def fetch_sediment_codes_for_zones(zone_ids: Optional[list] = None) -> dict:
    """Returns {zone_id: (code, texture_name) or None}. Synchronous
    wrapper -- safe to call from a CLI or from auto_refresh.py without
    an existing event loop."""
    zones = _load_zones(zone_ids)
    return asyncio.run(_gather(zones))


def ingest_sediment_codes(zone_ids: Optional[list] = None, store: Optional[StaticDatasetStore] = None) -> dict:
    """auto_refresh.py's entry point: fetch live, then upsert every zone
    that got a real classification. Zones with no SoilGrids coverage
    (rare -- open-water bboxes) are simply left un-upserted so an
    existing value (or None) isn't overwritten with nothing.

    Caller's responsibility to check `is_configured()` first -- this
    function assumes GEE credentials are set and will raise on the
    first zone's query if they aren't, same division of responsibility
    as fetch_river_level.py's ingest_river_levels()."""
    store = store or StaticDatasetStore()
    results = fetch_sediment_codes_for_zones(zone_ids)
    for zone_id, result in results.items():
        if result is None:
            continue
        code, _name = result
        store.upsert(zone_id, FIELD_NAME, float(code), "SoilGrids_GEE_texture_proxy_auto")
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zone-ids", nargs="*", default=None, help="Restrict to these zone_id(s); default: every zone in zones.py")
    parser.add_argument("--db", default="static_zone_data.db")
    args = parser.parse_args(argv)

    if not is_configured():
        print(
            f"fetch_sediment_type: {ENV_SERVICE_ACCOUNT} / {ENV_KEY_PATH} not set -- "
            "skipping. See this file's module docstring for the one-time GEE setup steps."
        )
        return 0

    store = StaticDatasetStore(db_path=args.db)
    results = ingest_sediment_codes(args.zone_ids, store)
    for zone_id, result in results.items():
        if result is None:
            print(f"{zone_id}: no SoilGrids coverage -- not overridden")
        else:
            code, name = result
            print(f"{zone_id}: sediment_type_code={code} ({name}, proxy)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
