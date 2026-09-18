"""fetch_historical_events.py — fully automated ingestion of PER-ZONE
historical_flood_count / historical_landslide_count /
historical_erosion_events / historical_cloudburst_count, replacing the
manual NDMA/SDMA/EM-DAT CSV path that used to be the only option (see
load_historical_counts.py, kept below as an optional manual override
for whenever someone actually has a cleaner district-level source).

WHY THIS EXISTS: load_historical_counts.py's docstring was honest that
NDMA/SDMA and EM-DAT are the best *per-district* sources but ship as
PDF/spreadsheet tables with no live API, and that turning them into a
zone_id,value CSV is a source-specific pandas job with no universal
script. That's still true and still the right call for a "we need the
authoritative district figure" use case. What closes the automation gap
for everything else is two sources that ARE free, live, and code-callable:

  1. GDACS (Global Disaster Alert and Coordination System,
     gdacs.org/gdacsapi) -- a live JSON feed of recent-to-current global
     disaster events (floods, tropical cyclones, earthquakes, droughts,
     wildfires, volcanoes), confirmed reachable with no API key. Used
     here for FLOOD (its "FL" event type) and, as a labeled proxy, for
     coastal EROSION (its "TC" tropical-cyclone type -- severe coastal
     erosion in India overwhelmingly co-occurs with cyclone landfall)
     and CLOUDBURST (a duration-filtered subset of "FL": events lasting
     <= `cloudburst_max_duration_days` are flagged as a flash-flood-like
     proxy, since GDACS has no dedicated cloudburst category).

  2. NASA's COOLR (Cooperative Open Online Landslide Repository,
     maps.nccs.nasa.gov) -- a live ArcGIS REST point catalog of reported
     rainfall-triggered landslides going back to 2007, queryable by
     bounding box with no API key. Used here for LANDSLIDE. Unlike the
     GDACS-based fields above, COOLR points are real per-incident
     locations, not a whole-country/region centroid -- so this is a
     meaningfully more precise per-zone signal, not just a proxy.

HONEST LIMITATION, stated plainly because it matters for how much to
trust these numbers: every GDACS event in the feed this script reads
carries ONE point geometry representing the centroid of the *entire*
affected extent (a "Flood in India" event spanning several states still
has just one lon/lat). Testing this against gdacs.org's own API
confirmed its "SEARCH" endpoint's documented `fromdate`/`todate`/
`eventlist`/`country` filters do not currently narrow the response --
every query returned the same recent global feed regardless of the
parameters sent. This script does NOT rely on those parameters: it pulls
the raw feed and does every filter (event type, India membership, date
window, zone proximity) itself in Python, which is more robust to that
behavior than trusting query params that may silently stop filtering.
The practical consequence is the FL/TC counts below are a "was a
GDACS-tracked event's broad-region centroid within roughly
`flood_buffer_deg`/`erosion_buffer_deg` degrees of this zone, within the
last `days_back` days" signal -- a real geographic + temporal filter,
meaningfully finer than ReliefWeb's country-only count, but still
coarser than a true per-district tally. Label discipline matters more
than precision here: the `source` string this writes always says
"GDACS" and, for erosion/cloudburst, says "proxy" explicitly, so nobody
downstream mistakes a coarse centroid match for verified NDMA data.

THEN RUN (usually you don't call this directly -- pipeline_runner.py
does, via auto_refresh.py, whenever a zone's counts are missing/stale):

    python3 -m data_pipeline.static_datasets.fetch_historical_events
    python3 -m data_pipeline.static_datasets.fetch_historical_events --zone-ids Z-ODISHA-PURI-01
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from .store import StaticDatasetStore

GDACS_SEARCH_URL = "https://www.gdacs.org/gdacsapi/api/Events/geteventlist/SEARCH"
COOLR_QUERY_URL = "https://maps.nccs.nasa.gov/mapping/rest/services/COOLR/COOLR_Events_Point/FeatureServer/0/query"

# A generous India+neighbours bbox for the one-shot COOLR pull (COOLR has
# no country filter of its own -- cheaper to fetch once broadly and match
# zones in Python than to issue one request per zone).
INDIA_REGION_BBOX = (68.0, 6.0, 98.0, 38.0)

FIELD_NAMES = {
    "FLOOD": "historical_flood_count",
    "LANDSLIDE": "historical_landslide_count",
    "EROSION": "historical_erosion_events",
    "CLOUDBURST": "historical_cloudburst_count",
}


def _load_zones(zone_ids: Optional[list]):
    from zones import list_zones

    registry = {z.zone_id: z for z in list_zones()}
    if zone_ids is None:
        return list(registry.values())
    unknown = [zid for zid in zone_ids if zid not in registry]
    if unknown:
        raise ValueError(f"Unknown zone_id(s): {unknown}. Known zones: {sorted(registry)}")
    return [registry[zid] for zid in zone_ids]


def _is_india(properties: dict) -> bool:
    if "India" in (properties.get("country") or ""):
        return True
    return any(c.get("iso3") == "IND" for c in properties.get("affectedcountries") or [])


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """GDACS timestamps observed in practice are plain ISO-8601 with no
    offset (e.g. "2026-08-09T01:00:00", not "...Z") -- GDACS's own docs
    and every sample response used to build this script agree they're
    UTC. Assume UTC for anything that comes back naive so this is always
    comparable to `cutoff` (timezone-aware) without raising."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _zone_matches_point(zone, lon: float, lat: float, buffer_deg: float) -> bool:
    return (
        zone.min_lon - buffer_deg <= lon <= zone.max_lon + buffer_deg
        and zone.min_lat - buffer_deg <= lat <= zone.max_lat + buffer_deg
    )


async def _fetch_gdacs_features(session) -> list:
    """One call to GDACS's recent-events feed. Returns the raw list of
    GeoJSON feature dicts, or [] on any failure -- a dead GDACS endpoint
    should degrade this to "no override" (ReliefWeb country-level count
    still applies), never crash a pipeline run.
    """
    try:
        async with session.get(
            GDACS_SEARCH_URL, params={"eventlist": "FL;TC"}, timeout=20
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data.get("features", [])
    except Exception:  # noqa: BLE001 - network/parspace errors -> empty, not a crash
        return []


async def _fetch_coolr_features(session) -> list:
    """One bbox query against NASA's COOLR landslide point catalog for
    the whole India region. Returns [] on failure (unreachable service,
    changed schema, ...) so LANDSLIDE simply falls back to ReliefWeb's
    country-level count instead of breaking the run.
    """
    min_lon, min_lat, max_lon, max_lat = INDIA_REGION_BBOX
    params = {
        "where": "1=1",
        "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "event_date",
        "returnGeometry": "true",
        "f": "json",
    }
    try:
        async with session.get(COOLR_QUERY_URL, params=params, timeout=20) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data.get("features", [])
    except Exception:  # noqa: BLE001
        return []


async def _gather_counts(
    zones: list,
    days_back: int,
    flood_buffer_deg: float,
    erosion_buffer_deg: float,
    landslide_buffer_deg: float,
    cloudburst_max_duration_days: float,
) -> dict:
    import aiohttp

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    counts = {zone.zone_id: {name: 0 for name in FIELD_NAMES.values()} for zone in zones}

    async with aiohttp.ClientSession() as session:
        gdacs_features, coolr_features = await asyncio.gather(
            _fetch_gdacs_features(session), _fetch_coolr_features(session)
        )

    for feat in gdacs_features:
        props = feat.get("properties", {})
        if not _is_india(props):
            continue
        event_type = props.get("eventtype")
        if event_type not in ("FL", "TC"):
            continue
        todate = _parse_dt(props.get("todate")) or _parse_dt(props.get("fromdate"))
        if todate is None or todate < cutoff:
            continue
        coords = (feat.get("geometry") or {}).get("coordinates")
        if not coords or len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]

        if event_type == "FL":
            fromdate = _parse_dt(props.get("fromdate"))
            duration_days = (todate - fromdate).total_seconds() / 86400 if fromdate else None
            for zone in zones:
                if not _zone_matches_point(zone, lon, lat, flood_buffer_deg):
                    continue
                counts[zone.zone_id]["historical_flood_count"] += 1
                if duration_days is not None and duration_days <= cloudburst_max_duration_days:
                    counts[zone.zone_id]["historical_cloudburst_count"] += 1
        elif event_type == "TC":
            for zone in zones:
                if _zone_matches_point(zone, lon, lat, erosion_buffer_deg):
                    counts[zone.zone_id]["historical_erosion_events"] += 1

    for feat in coolr_features:
        geom = feat.get("geometry") or {}
        lon, lat = geom.get("x"), geom.get("y")
        if lon is None or lat is None:
            continue
        for zone in zones:
            if _zone_matches_point(zone, lon, lat, landslide_buffer_deg):
                counts[zone.zone_id]["historical_landslide_count"] += 1

    return counts


def fetch_historical_counts_for_zones(
    zone_ids: Optional[list] = None,
    days_back: int = 730,
    flood_buffer_deg: float = 1.0,
    erosion_buffer_deg: float = 1.5,
    landslide_buffer_deg: float = 0.3,
    cloudburst_max_duration_days: float = 2.0,
) -> dict:
    """Returns {zone_id: {field_name: count}}. Synchronous wrapper --
    safe to call from a CLI or from auto_refresh.py without an existing
    event loop.
    """
    zones = _load_zones(zone_ids)
    return asyncio.run(
        _gather_counts(
            zones, days_back, flood_buffer_deg, erosion_buffer_deg,
            landslide_buffer_deg, cloudburst_max_duration_days,
        )
    )


def ingest_historical_counts(zone_ids: Optional[list] = None, store: Optional[StaticDatasetStore] = None, **kwargs) -> dict:
    """auto_refresh.py's entry point: fetch live, then upsert every
    field for every zone (including 0 -- "GDACS/COOLR tracked zero
    matching events in the window" is a real, meaningful value, not a
    missing one, same reasoning as mangrove_cover_pct's 0%)."""
    store = store or StaticDatasetStore()
    results = fetch_historical_counts_for_zones(zone_ids, **kwargs)
    for zone_id, field_values in results.items():
        for field_name, count in field_values.items():
            source = "GDACS_recent_events" if field_name != "historical_landslide_count" else "NASA_COOLR"
            if field_name in ("historical_erosion_events", "historical_cloudburst_count"):
                source += "_proxy"
            store.upsert(zone_id, field_name, float(count), source)
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zone-ids", nargs="*", default=None, help="Restrict to these zone_id(s); default: every zone in zones.py")
    parser.add_argument("--days-back", type=int, default=730, help="How far back to count GDACS flood/cyclone events (default 730 = ~2yr; see module docstring on why GDACS can't reliably go back further via its own date filters)")
    parser.add_argument("--flood-buffer-deg", type=float, default=1.0)
    parser.add_argument("--erosion-buffer-deg", type=float, default=1.5)
    parser.add_argument("--landslide-buffer-deg", type=float, default=0.3)
    parser.add_argument("--cloudburst-max-duration-days", type=float, default=2.0)
    parser.add_argument("--db", default="static_zone_data.db")
    args = parser.parse_args(argv)

    store = StaticDatasetStore(db_path=args.db)
    results = ingest_historical_counts(
        args.zone_ids, store,
        days_back=args.days_back,
        flood_buffer_deg=args.flood_buffer_deg,
        erosion_buffer_deg=args.erosion_buffer_deg,
        landslide_buffer_deg=args.landslide_buffer_deg,
        cloudburst_max_duration_days=args.cloudburst_max_duration_days,
    )
    for zone_id, field_values in results.items():
        print(f"{zone_id}: {field_values}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
