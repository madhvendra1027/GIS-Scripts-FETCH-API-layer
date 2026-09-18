"""fetch_flood_status_proxy.py — automated, free, no-key ingestion of a
PROXY value for flood_status_severity_code (FLOOD), for use while
fetch_flood_status.py's real source (Google's Flood Forecasting API) is
still gated behind a pilot waitlist that Google's own docs say can take
"several months" to clear (see that file's module docstring).

WHAT THIS IS, STATED PLAINLY: this is NOT Google's FloodStatus
classification, and does not claim to be. It is a same-scale (0-3)
stand-in built from a real, live, physically-grounded measurement --
GloFAS-modeled river discharge at this exact point, today, via the same
free Open-Meteo Flood API `river_discharge` (gis_fetcher's
`river_discharge` provider) already uses for `river_discharge_m3s`.

METHOD: pull ~10 years of that point's own historical daily discharge
from the same free endpoint, compute that point's own 90th/98th/99.5th
percentile discharge, and classify today's discharge against those
self-derived thresholds:
    below p90            -> 0 (roughly "nothing unusual for this river")
    p90  <= x <  p98      -> 1 (elevated for this river)
    p98  <= x <  p99.5    -> 2 (rare for this river)
    x >= p99.5            -> 3 (extremely rare for this river)
This is a common rough heuristic for flagging hydrological extremes
(comparing a value to its own station's historical distribution), NOT a
calibrated local flood-stage/return-period analysis the way a real
hydrological agency (or Google's model, which is trained against actual
inundation outcomes) would produce. Two rivers with identical discharge
in m3/s can have completely different real-world flood consequences
depending on channel geometry, floodplain development, embankments,
etc. -- none of which this proxy knows anything about. Treat a "2" or
"3" here as "worth a closer look", not as an evacuation trigger on its
own.

WHY THIS IS SEPARATE FROM fetch_flood_status.py, NOT A REPLACEMENT: the
real fetcher is left completely untouched and takes priority the moment
GOOGLE_FLOOD_API_KEY is configured -- see auto_refresh.py, which runs
this proxy first (always, no key needed) and then the real fetcher
second (only if configured), so a real value always overwrites a proxy
value for the same zone, never the other way around. Every value this
script writes is tagged with a source string ending in "_proxy" (see
FIELD_NAME's ingest below) so nothing downstream can mistake it for
Google's real classification.

WHY 10 YEARS, WHY THESE PERCENTILES: GloFAS reanalysis goes back to
1984, but 10 years is enough data (3650+ daily values) for stable
percentile estimates without a very large payload per zone. p90/p98/p99.5
were chosen to give 4 bins matching Google's 4-level severity enum
(NO_FLOODING/WARNING/DANGER/EXTREME_DANGER) -- these exact percentile
cut points are a judgment call, not a validated calibration against real
flood outcomes for Indian rivers. If real flood_status_severity_code
values (from fetch_flood_status.py, once the API key exists) are ever
compared against this proxy's output for the same zones, that comparison
would be the natural way to check whether these cut points need
adjusting.

HONEST LIMITATION: GloFAS is a 5km-grid river-routing model -- a
coordinate that doesn't sit on a mapped river channel returns near-zero
discharge with no real percentile spread, which this script cannot tell
apart from "genuinely calm river" (same limitation river_discharge.py
already documents for river_discharge_m3s itself). A zone with fewer
than `_MIN_HISTORY_POINTS` valid historical values is treated as "not
enough data to classify" and returns None -- no override this run --
rather than guessing off a thin sample.

THEN RUN (usually you don't call this directly -- auto_refresh.py does,
automatically, on every pipeline_runner.py run, no key needed):

    python3 -m data_pipeline.static_datasets.fetch_flood_status_proxy
    python3 -m data_pipeline.static_datasets.fetch_flood_status_proxy --zone-ids Z-ODISHA-PURI-01
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from typing import Optional

from .store import StaticDatasetStore

FLOOD_API_URL = "https://flood-api.open-meteo.com/v1/flood"
FIELD_NAME = "flood_status_severity_code"

_HISTORY_YEARS = 10
_MIN_HISTORY_POINTS = 365  # need at least ~1 year of valid daily values to trust a percentile

# Same retry/backoff shape as fetch_sediment_type.py and
# fetch_river_distance.py -- a shared public GloFAS/Open-Meteo endpoint
# under load can time out transiently.
_MAX_RETRIES = 2

_SEVERITY_LABELS = ["NO_FLOODING", "WARNING", "DANGER", "EXTREME_DANGER"]


def _classify(latest: float, history: list) -> int:
    """history must already exclude the latest/current value and any
    Nones. Returns an int 0-3 -- see module docstring for the cut
    points and their honest limitations."""
    sorted_hist = sorted(history)
    n = len(sorted_hist)

    def _percentile(p: float) -> float:
        idx = min(n - 1, max(0, int(round(p * (n - 1)))))
        return sorted_hist[idx]

    p90, p98, p995 = _percentile(0.90), _percentile(0.98), _percentile(0.995)
    if latest >= p995:
        return 3
    if latest >= p98:
        return 2
    if latest >= p90:
        return 1
    return 0


def _load_zones(zone_ids: Optional[list]):
    from zones import list_zones

    registry = {z.zone_id: z for z in list_zones()}
    if zone_ids is None:
        return list(registry.values())
    unknown = [zid for zid in zone_ids if zid not in registry]
    if unknown:
        raise ValueError(f"Unknown zone_id(s): {unknown}. Known zones: {sorted(registry)}")
    return [registry[zid] for zid in zone_ids]


async def _fetch_one_zone(session, zone) -> Optional[int]:
    lon = (zone.min_lon + zone.max_lon) / 2
    lat = (zone.min_lat + zone.max_lat) / 2
    end = date.today()
    start = end - timedelta(days=365 * _HISTORY_YEARS)
    query = {
        "latitude": lat,
        "longitude": lon,
        "daily": "river_discharge",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }

    data = None
    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with session.get(FLOOD_API_URL, params=query, timeout=30) as resp:
                resp.raise_for_status()
                data = await resp.json()
            break
        except Exception as exc:  # noqa: BLE001 - network/parse errors -> retry, then no override
            last_error = exc
            backoff = min(2 ** attempt, 15)
            if attempt < _MAX_RETRIES:
                print(f"fetch_flood_status_proxy: {zone.zone_id} attempt {attempt}/{_MAX_RETRIES} failed: {type(exc).__name__}: {exc} (retrying in {backoff}s)")
                await asyncio.sleep(backoff)
    if data is None:
        print(f"fetch_flood_status_proxy: {zone.zone_id} failed after {_MAX_RETRIES} attempts: {type(last_error).__name__}: {last_error}")
        return None

    values = data.get("daily", {}).get("river_discharge", []) or []
    valid = [v for v in values if v is not None]
    if not valid:
        return None

    latest = valid[-1]
    history = valid[:-1]
    if len(history) < _MIN_HISTORY_POINTS:
        print(f"fetch_flood_status_proxy: {zone.zone_id} only {len(history)} valid historical days (need {_MIN_HISTORY_POINTS}) -- not enough to classify")
        return None

    return _classify(latest, history)


async def _gather(zones: list) -> dict:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*[_fetch_one_zone(session, zone) for zone in zones])
    return {zone.zone_id: result for zone, result in zip(zones, results)}


def fetch_flood_status_proxies_for_zones(zone_ids: Optional[list] = None) -> dict:
    """Returns {zone_id: severity_code_0to3_or_None}."""
    zones = _load_zones(zone_ids)
    return asyncio.run(_gather(zones))


def ingest_flood_status_proxy(zone_ids: Optional[list] = None, store: Optional[StaticDatasetStore] = None) -> dict:
    """auto_refresh.py's entry point. Always runs (no key needed) --
    auto_refresh.py calls the REAL fetch_flood_status.ingest_flood_statuses()
    afterward when GOOGLE_FLOOD_API_KEY is configured, which overwrites
    this proxy's value for any zone it successfully covers."""
    store = store or StaticDatasetStore()
    results = fetch_flood_status_proxies_for_zones(zone_ids)
    for zone_id, code in results.items():
        if code is None:
            continue
        store.upsert(zone_id, FIELD_NAME, float(code), "GloFAS_discharge_percentile_proxy")
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zone-ids", nargs="*", default=None, help="Restrict to these zone_id(s); default: every zone in zones.py")
    parser.add_argument("--db", default="static_zone_data.db")
    args = parser.parse_args(argv)

    store = StaticDatasetStore(db_path=args.db)
    results = ingest_flood_status_proxy(args.zone_ids, store)
    for zone_id, code in results.items():
        if code is None:
            print(f"{zone_id}: not enough GloFAS history / no coverage -- not overridden")
        else:
            print(f"{zone_id}: flood_status_severity_code={code} ({_SEVERITY_LABELS[code]}, PROXY -- not Google's real classification)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
