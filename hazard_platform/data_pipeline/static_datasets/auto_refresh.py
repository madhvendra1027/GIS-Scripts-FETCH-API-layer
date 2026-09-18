"""auto_refresh.py — the orchestrator that makes the static layer
zero-touch. `pipeline_runner.py` calls `refresh_static_fields()` once
per zone at the top of every live run, before it ever reads
`StaticDatasetStore`. That call fetches exactly the fields that are
missing or older than `max_age_days` for that zone -- nothing else --
using the automated fetchers this package now has:

    fetch_sediment_type.py       -> sediment_type_code (SoilGrids-via-GEE
                                     proxy -- ONLY if GEE_SERVICE_ACCOUNT_EMAIL
                                     and GEE_SERVICE_ACCOUNT_KEY_PATH are set;
                                     see that file's docstring. Changed
                                     2026-09-18 from a keyless ISRIC-REST
                                     call after ISRIC paused that API --
                                     this field is no longer always-on)
    fetch_historical_events.py   -> historical_flood_count,
                                     historical_landslide_count,
                                     historical_erosion_events,
                                     historical_cloudburst_count
                                     (GDACS + NASA COOLR)
    fetch_river_distance.py      -> distance_to_river_m
                                     (OSM Overpass waterway ways)
    fetch_flood_status_proxy.py  -> flood_status_severity_code
                                     (GloFAS discharge-percentile proxy --
                                     ALWAYS runs, no key needed; see that
                                     file's docstring for the honest
                                     "this is a proxy, not Google's real
                                     classification" caveat)
    fetch_flood_status.py        -> flood_status_severity_code
                                     (Google's REAL classification --
                                     only if GOOGLE_FLOOD_API_KEY is set;
                                     overwrites the proxy's value per-zone
                                     wherever it succeeds, see below)
    fetch_river_level.py         -> river_level_m
                                     (Google Flood Forecasting API --
                                     ONLY if GOOGLE_FLOOD_API_KEY is set,
                                     see below)

flood_status_severity_code now has TWO fetchers, run in a fixed order
every time: the free proxy always runs first (so every zone gets some
value with zero setup), then the real Google fetcher runs second, but
only if GOOGLE_FLOOD_API_KEY is configured -- and only its non-None
results overwrite the proxy's, zone by zone. A real value always wins
over a proxy value; a proxy value is never allowed to overwrite a real
one. This replaces an earlier version of this module where
flood_status_severity_code was dropped entirely (no live source existed
at all at the time) -- see fetch_flood_status_proxy.py's own docstring
for why a discharge-percentile proxy is an honest thing to ship here,
and how it differs from fabricating a number.

NO SEPARATE COMMAND FOR ANY OF THESE, EVER: every fetcher above --
including river_level_m -- is invoked from right here, automatically,
whenever `pipeline_runner.py` runs for a zone whose fields are
missing/stale. Nobody ever needs to remember to run
`python3 -m data_pipeline.static_datasets.fetch_river_level` (or any of
its siblings) by hand; the module-level `main()` in each of those files
exists only for a one-off manual check, not as a required step. The
*only* manual, one-time action left for river_level_m is the Google
Flood Forecasting API signup (paperwork, not a command -- see
fetch_river_level.py's module docstring for the exact steps, or
STATIC_DATASETS.md's "Getting river_level_m live" section) -- once
`GOOGLE_FLOOD_API_KEY` is set in the environment, this module picks it
up on the very next run with zero code changes.

WHAT THIS STILL DOESN'T COVER: shoreline_change_rate_m_per_yr and
mangrove_cover_pct. Both stay one-time-download + `ingest_dataset.py`
(see STATIC_DATASETS.md's "Why these two can't be automated further" --
that section was re-checked again this update, including India's
Bhuvan WFS/WMS thematic-erosion layer and the Global Mangrove Watch
Earth Engine asset as two live-*capable* leads that exist but aren't a
documented, stable, point-query REST endpoint the way the Flood
Forecasting API is -- so building an automated fetcher against either
right now would be the same "scraper against an undocumented interface"
mistake this repo already rejected once for CWC/India-WRIS). Once
ingested, those two fields are picked up by `pipeline_runner.py` exactly
like the auto-fetched ones (same `StaticDatasetStore`, same merge step)
-- this module simply has nothing to *fetch* for them yet.

WHY A SEPARATE STALENESS CHECK PER FIELD, NOT PER ZONE: these fields
change on very different timescales (sediment type: effectively never;
distance_to_river_m: effectively never; historical counts: meaningfully
every few months; river_level_m: hourly, if it's even enabled) but this
module still checks all of them with the same `max_age_days` for
simplicity in a hackathon-scoped repo -- see `DEFAULT_MAX_AGE_DAYS`'s
docstring below for the reasoning on the one shared default, and pass a
smaller value explicitly if you want a faster-changing field (river
level, if enabled) refreshed more eagerly than the others.
"""

from __future__ import annotations

from typing import Optional

from .fetch_flood_status_proxy import FIELD_NAME as FLOOD_STATUS_FIELD_NAME
from .fetch_flood_status_proxy import ingest_flood_status_proxy
from .fetch_flood_status import ingest_flood_statuses as ingest_flood_statuses_real
from .fetch_historical_events import FIELD_NAMES as HISTORICAL_FIELD_NAMES
from .fetch_historical_events import ingest_historical_counts
from .fetch_river_distance import FIELD_NAME as RIVER_DISTANCE_FIELD_NAME
from .fetch_river_distance import ingest_river_distances
from .fetch_river_level import FIELD_NAME as RIVER_LEVEL_FIELD_NAME
from .fetch_river_level import ingest_river_levels
from .fetch_river_level import is_configured as river_level_is_configured
from .fetch_sediment_type import FIELD_NAME as SEDIMENT_FIELD_NAME
from .fetch_sediment_type import ingest_sediment_codes
from .fetch_sediment_type import is_configured as sediment_is_configured
from .store import StaticDatasetStore

# 180 days: long enough that a normal demo/pilot run never re-fetches on
# every invocation (sediment type / river distance in particular never
# need to), short enough that a long-running deployment's
# historical-event counts (or river_level_m, if enabled) don't go stale
# for a whole year before anyone notices. Pass a smaller value explicitly
# (pipeline_runner.py's --static-max-age-days) for a deployment that
# wants faster-changing fields refreshed more often than that.
DEFAULT_MAX_AGE_DAYS = 180

_ALWAYS_AUTO_FIELDS = [RIVER_DISTANCE_FIELD_NAME, FLOOD_STATUS_FIELD_NAME, *HISTORICAL_FIELD_NAMES.values()]


def _active_auto_fields() -> list:
    """The always-on fields, plus sediment_type_code and/or river_level_m
    once their one-time signups are done. sediment_type_code moved out
    of the always-on list on 2026-09-18: it used to query ISRIC's REST
    API directly (free, keyless), but that API is now paused
    indefinitely, so this field switched to Google Earth Engine and
    picked up the same "needs one-time setup" gate river_level_m already
    had -- see fetch_sediment_type.py's `is_configured()`. Checked fresh
    on every call (not cached at import time) so setting the env vars
    mid-session and re-running takes effect immediately, no restart
    needed."""
    fields = list(_ALWAYS_AUTO_FIELDS)
    if sediment_is_configured():
        fields.append(SEDIMENT_FIELD_NAME)
    if river_level_is_configured():
        fields.append(RIVER_LEVEL_FIELD_NAME)
    return fields


def _zones_needing_refresh(zone_ids: list, store: StaticDatasetStore, max_age_days: float, active_fields: list) -> dict[str, list]:
    """{zone_id: [field_name, ...]} for only the zone/field pairs that
    are actually missing or stale -- so a zone that already has fresh
    data from an hour ago doesn't trigger a needless network round trip
    just because another zone in the same batch does."""
    needed: dict[str, list] = {}
    for zone_id in zone_ids:
        stale_fields = [f for f in active_fields if store.needs_refresh(zone_id, f, max_age_days)]
        if stale_fields:
            needed[zone_id] = stale_fields
    return needed


def refresh_static_fields(
    zone_ids: list,
    store: StaticDatasetStore,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
) -> dict:
    """Entry point `pipeline_runner.py` calls. For the given `zone_ids`,
    figures out which zones have any auto-fetchable field missing/stale,
    and if so re-fetches ALL auto-fetchable fields for just those zones
    in one batched call each to the fetchers below (all of which already
    accept a `zone_ids` filter, so this never re-fetches a zone that's
    already fresh).

    river_level_m is only ever included in "auto-fetchable" -- and
    therefore only ever costs a network call -- once
    `GOOGLE_FLOOD_API_KEY` is set (see `_active_auto_fields()`); with no
    key set, this behaves exactly as it did before river_level_m
    existed.

    Returns a summary dict: {"refreshed_zones": [...], "sediment": {...},
    "historical": {...}, "river_distance": {...}, "river_level": {...}}
    -- {} for a sub-key if nothing needed refreshing this call (or if
    river_level_m isn't configured this run), so callers/tests can check
    `if summary["refreshed_zones"]` cheaply instead of inspecting nested
    dicts.
    """
    active_fields = _active_auto_fields()
    needing_refresh = _zones_needing_refresh(zone_ids, store, max_age_days, active_fields)
    if not needing_refresh:
        return {"refreshed_zones": [], "sediment": {}, "historical": {}, "river_distance": {}, "flood_status": {}, "river_level": {}}

    refresh_ids = sorted(needing_refresh)
    sediment_results = ingest_sediment_codes(refresh_ids, store) if sediment_is_configured() else {}
    historical_results = ingest_historical_counts(refresh_ids, store)
    river_distance_results = ingest_river_distances(refresh_ids, store)
    # Proxy runs first (always, no key needed) so every zone gets SOME
    # flood_status_severity_code value; the real fetch runs second and
    # overwrites it per-zone wherever Google actually has a status --
    # see fetch_flood_status_proxy.py's module docstring for why this
    # order, never the reverse, is the one that matters.
    flood_status_results = ingest_flood_status_proxy(refresh_ids, store)
    if river_level_is_configured():  # same GOOGLE_FLOOD_API_KEY as river_level_m
        real_flood_status_results = ingest_flood_statuses_real(refresh_ids, store)
        flood_status_results.update({k: v for k, v in real_flood_status_results.items() if v is not None})
    river_level_results = ingest_river_levels(refresh_ids, store) if river_level_is_configured() else {}

    return {
        "refreshed_zones": refresh_ids,
        "sediment": sediment_results,
        "historical": historical_results,
        "river_distance": river_distance_results,
        "flood_status": flood_status_results,
        "river_level": river_level_results,
    }


def refresh_all_zones(store: Optional[StaticDatasetStore] = None, max_age_days: float = DEFAULT_MAX_AGE_DAYS) -> dict:
    """Convenience for a one-off batch refresh across every zone in
    zones.py (e.g. pre-warming before an offline demo) instead of
    letting each zone refresh lazily on its own first pipeline_runner.py
    run."""
    from zones import list_zones

    store = store or StaticDatasetStore()
    zone_ids = [z.zone_id for z in list_zones()]
    return refresh_static_fields(zone_ids, store, max_age_days=max_age_days)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Batch-refresh sediment_type_code + historical_*_count for every zone (or a subset) "
        "whose stored value is missing or older than --max-age-days. Normally you don't need to run this "
        "directly -- pipeline_runner.py calls it automatically, per-zone, on every live run."
    )
    parser.add_argument("--zone-ids", nargs="*", default=None, help="Restrict to these zone_id(s); default: every zone in zones.py")
    parser.add_argument("--max-age-days", type=float, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--db", default="static_zone_data.db")
    args = parser.parse_args(argv)

    store = StaticDatasetStore(db_path=args.db)
    if args.zone_ids is None:
        summary = refresh_all_zones(store, max_age_days=args.max_age_days)
    else:
        summary = refresh_static_fields(args.zone_ids, store, max_age_days=args.max_age_days)

    if not summary["refreshed_zones"]:
        print("Nothing to refresh -- every requested zone's auto-fetched fields are already fresh.")
    else:
        print(f"Refreshed: {summary['refreshed_zones']}")
        if sediment_is_configured():
            print(f"  sediment_type_code:   {summary['sediment']}")
        else:
            print(f"  sediment_type_code:   skipped -- {SEDIMENT_FIELD_NAME}'s GEE_SERVICE_ACCOUNT_EMAIL/"
                  f"GEE_SERVICE_ACCOUNT_KEY_PATH not set (see fetch_sediment_type.py's docstring for the "
                  f"one-time GEE setup)")
        print(f"  historical counts:    {summary['historical']}")
        print(f"  distance_to_river_m:  {summary['river_distance']}")
        print(f"  flood_status_severity_code (proxy, always-on): {summary['flood_status']}")
        if river_level_is_configured():
            print(f"  river_level_m:        {summary['river_level']}")
        else:
            print(f"  river_level_m:        skipped -- {RIVER_LEVEL_FIELD_NAME}'s GOOGLE_FLOOD_API_KEY not set "
                  f"(see fetch_river_level.py's docstring for the one-time signup)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
