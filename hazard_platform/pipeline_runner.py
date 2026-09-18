"""pipeline_runner.py — the live end-to-end glue this repo was missing.

Before this file: gis_fetcher could fetch live data, normalize.py/
cleaning.py could process it, and HazardReadingStore could persist it —
but nothing called them in that order with real network data.
example_run.py deliberately uses hand-typed sample parameters (its own
docstring says so) to prove the scoring/classification/prioritization
stages work without a network call; it does not exercise gis_fetcher,
normalize.py, or cleaning.py at all.

This script is the piece that makes it a real pipeline:

    gis_fetcher.hazard_map.fetch_for_hazard()  (live network calls)
        -> data_pipeline.normalize.*_to_*_fields()   (rename to locked fields)
        -> data_pipeline.cleaning.clean_reading()    (validate/impute/flag)
        -> data_pipeline.hazard_reading_store.HazardReadingStore.save()

Run it once per zone, then hit GET /api/zone-status/{zone_id} (backend/api.py)
to see the scored result — see the README "Run it once, live" section.

Requires the gis_fetcher package importable on PYTHONPATH alongside this
package (see README's install steps) — this file has a clear ImportError
at the top if it isn't.
"""

from __future__ import annotations

# Loads .env from this file's directory (see README's "API keys" section)
# so AGROMONITORING_API_KEY / GOOGLE_FLOOD_API_KEY are in os.environ before
# gis_fetcher or fetch_river_level.py ever read them -- replaces having to
# `set`/`setx` them by hand in every new terminal.
from dotenv import load_dotenv
load_dotenv()

import argparse
import sys
from datetime import datetime, timezone
from typing import Optional

try:
    from gis_fetcher.core.base import BBox
    from gis_fetcher.hazard_map import fetch_for_hazard, providers_for_hazard
except ImportError as exc:  # pragma: no cover - clearer failure than a bare traceback
    raise ImportError(
        "gis_fetcher is not importable. Install it alongside hazard_platform first "
        "(pip install -e ../gis_fetcher, or add it to PYTHONPATH) — see README.md."
    ) from exc

from data_pipeline import normalize
from data_pipeline.cleaning import ZoneHistory, clean_reading
from data_pipeline.hazard_reading_store import HazardReadingStore
from data_pipeline.models import DataQuality, HazardReading, HazardType
from data_pipeline.static_datasets.auto_refresh import DEFAULT_MAX_AGE_DAYS, refresh_static_fields
from data_pipeline.static_datasets.store import StaticDatasetStore
from zones import Zone, get_zone, zone_from_point


class _EmptyFeature:
    """Stand-in for gis_fetcher's Feature when a provider fails or
    returns nothing, so normalize.py's *_to_*_fields() functions still
    get something with a `.properties` dict to call `.get()` on instead
    of crashing on a bare `None`. Every _get() lookup on this then
    resolves to None, which cleaning.py's imputation logic already knows
    how to handle -- see the comment in ingest_zone() below on why an
    explicit None beats a missing key here.
    """

    def __init__(self, source: str):
        self.properties: dict = {}
        self.source = source

# Per-hazard: which provider's features feed which normalize function(s).
# A provider can feed more than one function (e.g. `weather` feeds both
# a hazard-specific function here); a hazard can use more than one
# provider. This table is the one place that has to stay in sync with
# both gis_fetcher/hazard_map.py's HAZARD_PROVIDERS and this package's
# normalize.py — if you add a provider to one, add its mapping here too.
_NORMALIZERS: dict[str, dict[str, callable]] = {
    "FLOOD": {
        "weather": normalize.weather_to_flood_fields,
        "elevation": normalize.elevation_to_shared_fields,
        "river_discharge": normalize.river_discharge_to_flood_fields,
        "land_hydrology": normalize.land_hydrology_to_flood_fields,
        "historical_events": lambda f: normalize.historical_to_fields(f, "FLOOD"),
    },
    "LANDSLIDE": {
        "weather": normalize.weather_to_landslide_fields,
        "slope": normalize.slope_to_landslide_fields,
        "soil": normalize.soil_to_landslide_fields,
        "land_hydrology": normalize.land_hydrology_to_landslide_fields,
        "vegetation": normalize.vegetation_to_landslide_fields,
        "historical_events": lambda f: normalize.historical_to_fields(f, "LANDSLIDE"),
    },
    "EROSION": {
        "marine": normalize.marine_to_erosion_fields,
        "osm": normalize.osm_to_erosion_fields,  # takes list[Feature], not one
        "historical_events": lambda f: normalize.historical_to_fields(f, "EROSION"),
    },
    "CLOUDBURST": {
        "weather": normalize.weather_to_cloudburst_fields,
        "elevation": normalize.elevation_to_shared_fields,
        "historical_events": lambda f: normalize.historical_to_fields(f, "CLOUDBURST"),
    },
}


def seed_history_from_store(zone_id: str, store: HazardReadingStore, history: ZoneHistory) -> None:
    """Load each hazard's most recent saved reading for this zone into
    `history` before a live run starts, so a field that fails to fetch
    THIS run (a flaky provider, a rate limit) falls back to the last
    real measurement this pipeline ever saw for this zone -- not
    straight to cleaning.py's crude REGIONAL_DEFAULTS. Without this,
    ZoneHistory starts empty on every process run (main() below creates
    a fresh one each invocation), so "last known good value" only ever
    covered fields shared *within* one run (e.g. elevation_m appearing
    in both FLOOD and CLOUDBURST), never across separate pipeline runs
    -- which is the common case for a scheduled/cron'd live pipeline.
    """
    for hazard_type in HazardType:
        reading = store.latest_for_zone(zone_id, hazard_type)
        if reading is None:
            continue
        for field_name, value in reading.parameters.items():
            if isinstance(value, (int, float)):
                history.update(zone_id, field_name, float(value))


def ingest_zone(
    zone_id: str,
    store: HazardReadingStore,
    history: ZoneHistory,
    static_store: Optional[StaticDatasetStore] = None,
    auto_refresh_static: bool = True,
    static_max_age_days: float = DEFAULT_MAX_AGE_DAYS,
) -> dict:
    """Fetch, normalize, clean, and persist all 4 hazards' readings for
    one zone. Returns a per-hazard summary dict for printing/inspection —
    this is what you check after running the pipeline once.

    `auto_refresh_static=True` (the default) is what makes the static
    layer zero-touch: before reading StaticDatasetStore, this calls
    `refresh_static_fields()` for just this zone_id, which live-fetches
    sediment_type_code and all four historical_*_count fields the first
    time this zone is seen (or whenever the stored value is older than
    `static_max_age_days`) — see auto_refresh.py's docstring for exactly
    what is and isn't covered by that call. Pass False to skip this (e.g.
    an offline demo, or a caller that already refreshed in bulk).
    """
    zone = get_zone(zone_id)
    bbox = BBox(zone.min_lon, zone.min_lat, zone.max_lon, zone.max_lat)
    summary: dict[str, dict] = {}

    if auto_refresh_static and static_store is not None:
        refresh_static_fields([zone_id], static_store, max_age_days=static_max_age_days)

    static_fields = static_store.get_all_for_zone(zone_id) if static_store else {}

    for hazard_type in HazardType:
        hazard_name = hazard_type.value
        expected_providers = providers_for_hazard(hazard_name)
        results = fetch_for_hazard(hazard_name, bbox)

        field_dicts = []
        provider_status = {}
        for result in results:
            provider_status[result.provider] = "ok" if result.ok else f"FAILED: {result.error}"
            normalizer = _NORMALIZERS[hazard_name].get(result.provider)
            if normalizer is None:
                continue
            if result.provider == "osm":
                # osm_to_erosion_fields takes the whole feature list (it
                # scans for a coastline tag) -- an empty list on failure
                # still returns its dict with distance_to_coast_m=None,
                # which is what we want cleaning.py to see and impute.
                field_dicts.append(normalizer(result.features if result.ok else []))
                continue
            if result.ok and result.features:
                field_dicts.append(normalizer(result.features[0]))
            else:
                # Provider failed or returned nothing: still call the
                # normalizer with an empty-properties stand-in so the
                # field comes through as an explicit None rather than
                # being absent from raw_parameters entirely -- absent
                # keys silently skip cleaning.py's imputation/fallback
                # logic, while an explicit None goes through it properly.
                field_dicts.append(normalizer(_EmptyFeature(source=result.provider)))

        # Static overrides go LAST so merge_shared_fields()'s "later
        # dict wins on non-None" rule lets a real one-time-ingested
        # value (e.g. shoreline_change_rate_m_per_yr from the loader
        # scripts) win over a live source's None -- but a live source
        # that DID return a real value for the same field still wins
        # over a stale static one, since static_fields only carries
        # non-None entries in the first place (see StaticDatasetStore
        # .get_all_for_zone) and merge_shared_fields only overwrites
        # with non-None values.
        raw_parameters = normalize.merge_shared_fields(*field_dicts, static_fields)
        cleaned = clean_reading(zone_id, raw_parameters, datetime.now(timezone.utc), history)

        data_quality = DataQuality.RAW
        if cleaned.is_stale:
            data_quality = DataQuality.STALE
        elif cleaned.imputed_fields:
            data_quality = DataQuality.IMPUTED

        store.save(HazardReading(
            zone_id=zone_id,
            hazard_type=hazard_type,
            source="+".join(expected_providers),
            recorded_at=datetime.now(timezone.utc),
            parameters=cleaned.parameters,
            data_quality=data_quality,
        ))

        summary[hazard_name] = {
            "providers_called": expected_providers,
            "provider_status": provider_status,
            "parameters": cleaned.parameters,
            "imputed_fields": cleaned.imputed_fields,
            "dropped_fields": cleaned.dropped_fields,
        }

    return summary


def ingest_point(
    lat: float,
    lon: float,
    store: HazardReadingStore,
    history: ZoneHistory,
    static_store: Optional[StaticDatasetStore] = None,
    radius_km: float = 5.0,
    auto_refresh_static: bool = True,
    static_max_age_days: float = DEFAULT_MAX_AGE_DAYS,
) -> tuple[Zone, dict]:
    """The "click anywhere on the map" entry point: turns a raw lat/lon
    into a real zone via `zones.zone_from_point()` (no pre-registered
    zone_id required), seeds its history from any prior readings under
    that same derived zone_id, then runs the exact same live
    fetch -> normalize -> clean -> save pipeline as `ingest_zone()`.

    Returns `(zone, summary)` so callers (backend/api.py) have the
    derived zone_id/name/bbox to hand back to the frontend alongside the
    per-hazard summary.
    """
    zone = zone_from_point(lat, lon, radius_km=radius_km)
    seed_history_from_store(zone.zone_id, store, history)
    summary = ingest_zone(
        zone.zone_id,
        store,
        history,
        static_store=static_store,
        auto_refresh_static=auto_refresh_static,
        static_max_age_days=static_max_age_days,
    )
    return zone, summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch live data for one zone, run it through normalize -> "
        "cleaning -> HazardReadingStore, and print a per-hazard summary."
    )
    parser.add_argument("zone_id", help="e.g. Z-BIHAR-PATNA-01 -- see zones.py for the full list")
    parser.add_argument("--db", default="hazard_readings.db", help="SQLite path (default: ./hazard_readings.db)")
    parser.add_argument(
        "--static-db", default="static_zone_data.db",
        help="SQLite path for static fields (default: ./static_zone_data.db). Sediment type and "
        "historical counts are now auto-fetched live into this on first use (see auto_refresh.py); "
        "see data_pipeline/static_datasets/load_*.py only if you want to override with a real "
        "manually-sourced dataset instead.",
    )
    parser.add_argument(
        "--no-auto-refresh-static", action="store_true",
        help="Skip the automatic sediment/historical-count live refresh (e.g. for an offline demo).",
    )
    parser.add_argument(
        "--static-max-age-days", type=float, default=DEFAULT_MAX_AGE_DAYS,
        help=f"Re-fetch sediment/historical fields if the stored value is older than this (default {DEFAULT_MAX_AGE_DAYS}).",
    )
    args = parser.parse_args(argv)

    store = HazardReadingStore(db_path=args.db)
    static_store = StaticDatasetStore(db_path=args.static_db)
    history = ZoneHistory()
    seed_history_from_store(args.zone_id, store, history)

    print(f"Ingesting live data for {args.zone_id} ...\n")
    summary = ingest_zone(
        args.zone_id, store, history, static_store=static_store,
        auto_refresh_static=not args.no_auto_refresh_static,
        static_max_age_days=args.static_max_age_days,
    )

    for hazard_name, info in summary.items():
        print(f"--- {hazard_name} ---")
        print("  providers:", info["provider_status"])
        print("  parameters:", info["parameters"])
        if info["imputed_fields"]:
            print("  imputed (missing, filled with fallback):", info["imputed_fields"])
        if info["dropped_fields"]:
            print("  dropped (out of plausible range):", info["dropped_fields"])
        still_none = [k for k, v in info["parameters"].items() if v is None]
        if still_none:
            print("  still None (no source at all -- see STATIC_DATASETS.md):", still_none)
        print()

    print(f"Saved to {args.db}. Now run the API and GET /api/zone-status/{args.zone_id} to see the scored result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
