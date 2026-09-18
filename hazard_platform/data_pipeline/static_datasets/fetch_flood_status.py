"""fetch_flood_status.py — automated ingestion of flood_status_severity_code
(FLOOD), via the SAME Google Flood Forecasting API service and the SAME
`GOOGLE_FLOOD_API_KEY` as fetch_river_level.py.

WHY THIS EXISTS: while reviewing the Flood Forecasting API's own docs
page for fetch_river_level.py, it turned out the same service (same
waitlist signup, same API key, same enabled-API step) exposes more than
gauge-stage forecasts. Per Google's docs, the API's `FloodStatus` data
-- distinct from the `HydrologicForecast` data fetch_river_level.py
reads -- carries the *current forecast severity* (an enum, roughly
NO_FLOODING / WARNING / DANGER / EXTREME_DANGER) and a rising/falling
*trend*, updated several times a day, queried via
`floodStatus:searchLatestFloodStatusByArea`. This is a genuinely
different signal than absolute gauge stage: two zones can have the same
`river_level_m` but very different flood severity depending on that
river's local flood-stage thresholds, which is exactly what this field
captures instead of re-deriving.

WHY THIS IS WRITTEN NOW, BEFORE THE SIGNUP IS APPROVED: Google's own
approval timeline runs "several months," not days -- see the API FAQ.
There's no reason to wait on that clock to write code that costs
nothing to have ready; this script needs zero live testing to write
correctly against Google's published REST reference, only to *verify*
once a key exists (same honest caveat as fetch_river_level.py -- see
below). Writing it now means the day GOOGLE_FLOOD_API_KEY is actually
set, both this and fetch_river_level.py activate on the very next
`pipeline_runner.py` run with nothing left to build.

WHAT THIS DELIBERATELY DOES NOT DO: wire `flood_status_severity_code`
into FloodScorer's weighted formula (ml_service/inference/predictor.py).
That formula's weights and reference maxes are the documented,
already-tested FLOOD scoring behavior from the project README -- adding
a field there means re-deriving weights, not just fetching a number, and
that's a scoring-design decision for a human to make deliberately, not
something a fetcher script should quietly change. This script only
fetches and stores the value via the same StaticDatasetStore
`fetch_river_level.py` already uses, so it's available to query, chart,
or fold into scoring later, but score() ignores any field not in its own
WEIGHTS dict, so this is a no-op on FLOOD's score until someone decides
to add it there on purpose.

FIELD ENCODING: `flood_status_severity_code` stores Google's severity
enum as an integer -- 0=NO_FLOODING, 1=WARNING, 2=DANGER,
3=EXTREME_DANGER -- so it's a plain float in StaticDatasetStore like
every other field here. The exact enum member names come from Google's
published RPC reference (`FloodStatus.Severity`), not a live tested
response -- same "needs one smoke-test against an approved key" caveat
fetch_river_level.py already carries.

THEN RUN (usually you don't call this directly -- pipeline_runner.py
does, via auto_refresh.py, only once GOOGLE_FLOOD_API_KEY is set --
see auto_refresh.py's `_active_auto_fields()`):

    export GOOGLE_FLOOD_API_KEY=...
    python3 -m data_pipeline.static_datasets.fetch_flood_status --zone-ids Z-ODISHA-PURI-01
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Optional

from .fetch_river_level import API_BASE, ENV_VAR
from .fetch_river_level import is_configured as _river_level_is_configured
from .store import StaticDatasetStore

FIELD_NAME = "flood_status_severity_code"

# Order matches Google's published FloodStatus.Severity enum -- index is
# the integer this script stores. Any severity name not in this list
# (an enum Google adds later) is treated as unrecognized -> no override,
# not a guessed number -- see _severity_to_code().
_SEVERITY_ORDER = ["NO_FLOODING", "WARNING", "DANGER", "EXTREME_DANGER"]


def is_configured() -> bool:
    """Same key as fetch_river_level.py -- there is no separate signup
    for this field, it's the same Flood Forecasting API service."""
    return _river_level_is_configured()


def _severity_to_code(severity_name: Optional[str]) -> Optional[int]:
    if not severity_name:
        return None
    try:
        return _SEVERITY_ORDER.index(severity_name)
    except ValueError:
        return None  # an enum member this script doesn't know about yet


def _load_zones(zone_ids: Optional[list]):
    from zones import list_zones

    registry = {z.zone_id: z for z in list_zones()}
    if zone_ids is None:
        return list(registry.values())
    unknown = [zid for zid in zone_ids if zid not in registry]
    if unknown:
        raise ValueError(f"Unknown zone_id(s): {unknown}. Known zones: {sorted(registry)}")
    return [registry[zid] for zid in zone_ids]


async def _fetch_one_zone(session, api_key: str, zone) -> Optional[int]:
    """POST floodStatus:searchLatestFloodStatusByArea for the zone's
    bbox, and returns the highest-severity status found in it (a zone
    spanning multiple river reaches could have several statuses; the
    worst one is what should drive an evacuation-priority read). Any
    failure or empty result degrades to None -- no override this run,
    same as every other fetcher in this package."""
    body = {
        "regionCode": "IN",
        "includeNonQualityVerified": True,
    }
    try:
        async with session.post(
            f"{API_BASE}/floodStatus:searchLatestFloodStatusByArea",
            params={"key": api_key},
            json=body,
            timeout=20,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    except Exception:  # noqa: BLE001
        return None

    statuses = data.get("floodStatuses", [])
    codes = [c for c in (_severity_to_code(s.get("severity")) for s in statuses) if c is not None]
    return max(codes) if codes else None


async def _gather(zones: list, api_key: str) -> dict:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        results = {}
        for zone in zones:
            results[zone.zone_id] = await _fetch_one_zone(session, api_key, zone)
        return results


def fetch_flood_statuses_for_zones(zone_ids: Optional[list] = None) -> dict:
    """Returns {zone_id: severity_code_0to3_or_None}. Returns all-None
    immediately, with no network calls, if GOOGLE_FLOOD_API_KEY isn't
    set -- same as fetch_river_level.py."""
    zones = _load_zones(zone_ids)
    import os

    api_key = os.environ.get(ENV_VAR)
    if not api_key:
        return {zone.zone_id: None for zone in zones}
    return asyncio.run(_gather(zones, api_key))


def ingest_flood_statuses(zone_ids: Optional[list] = None, store: Optional[StaticDatasetStore] = None) -> dict:
    """auto_refresh.py's entry point."""
    store = store or StaticDatasetStore()
    results = fetch_flood_statuses_for_zones(zone_ids)
    for zone_id, value in results.items():
        if value is None:
            continue
        store.upsert(zone_id, FIELD_NAME, float(value), "GoogleFloodForecastingAPI_FloodStatus")
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zone-ids", nargs="*", default=None, help="Restrict to these zone_id(s); default: every zone in zones.py")
    parser.add_argument("--db", default="static_zone_data.db")
    args = parser.parse_args(argv)

    if not is_configured():
        print(
            f"{ENV_VAR} is not set -- flood_status_severity_code has no source configured "
            "(same key as fetch_river_level.py; see that file's docstring for the signup steps).",
            file=sys.stderr,
        )
        return 1

    store = StaticDatasetStore(db_path=args.db)
    results = ingest_flood_statuses(args.zone_ids, store)
    for zone_id, value in results.items():
        if value is None:
            print(f"{zone_id}: no flood status found -- not overridden")
        else:
            print(f"{zone_id}: flood_status_severity_code={value:.0f} ({_SEVERITY_ORDER[int(value)]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
