"""fetch_river_distance.py — fully automated ingestion of
distance_to_river_m (FLOOD), closing the gap normalize.py's
weather_to_flood_fields() previously left as a hardcoded None with no
fetcher behind it at all (unlike distance_to_coast_m, which at least had
osm_to_erosion_fields() wired up for EROSION).

WHY THIS EXISTS / WHY THIS SOURCE: there's no dedicated "distance to
nearest river" REST API anywhere, live or otherwise. But OpenStreetMap's
`waterway` tag (river/stream ways) is free, live, no-key, queryable via
the same public Overpass API `gis_fetcher`'s `osm` provider already uses
(see gis_fetcher/providers/osm_vector.py) -- this script talks to
Overpass directly (no gis_fetcher import, matching fetch_sediment_type.py
and fetch_historical_events.py's existing no-hard-dependency convention),
requesting full way geometry (`out geom;`) so distance can be computed
without a second round-trip to resolve node IDs.

METHOD: pad each zone's bbox by `_SEARCH_PAD_DEG`, ask Overpass for every
`waterway=river` or `waterway=stream` way inside that padded box, then
compute the shortest point-to-segment distance from the zone's bbox
center to any segment of any returned way -- in meters, via a local
equirectangular (flat-earth) projection centered on the zone. That
projection is accurate to well under 1% error at the ~10-50km scale this
field's VALID_RANGES caps out at (see cleaning.py), so it's not worth
pulling in a real geodesic library for this.

HONEST LIMITATION: this is exactly as complete as OpenStreetMap's river
mapping is for a given area -- generally very good for India's major and
most secondary rivers, but a genuinely nearby minor stream that no one
has traced into OSM yet would be silently invisible to this query, same
as any other OSM-dependent source. A zone with truly no `waterway` way
within `_SEARCH_PAD_DEG` returns None (a real "no OSM-mapped river
nearby" signal, not a crash) rather than guessing -- see
`_fetch_one_zone()`.

THEN RUN (usually you don't call this directly -- pipeline_runner.py
does, via auto_refresh.py, whenever a zone's distance_to_river_m is
missing/stale):

    python3 -m data_pipeline.static_datasets.fetch_river_distance
    python3 -m data_pipeline.static_datasets.fetch_river_distance --zone-ids Z-ODISHA-PURI-01
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from typing import Optional

from .store import StaticDatasetStore

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
FIELD_NAME = "distance_to_river_m"

# 0.5deg (~55km at India's latitudes) -- generous enough that a genuine
# "far from any river" zone (cleaning.py's VALID_RANGES caps this field
# at 50000m) still gets a real answer instead of a false None, while
# staying a single Overpass call per zone (this shared public instance
# rate-limits aggressively -- see osm_vector.py's docstring -- so this
# script also sleeps `_OVERPASS_MIN_INTERVAL_S` between zone queries in
# a batch, same courtesy gis_fetcher's config/providers.yaml already
# asks of the `osm` provider).
_SEARCH_PAD_DEG = 0.5
_OVERPASS_MIN_INTERVAL_S = 2.0

# gis_fetcher's `osm` provider (config/providers.yaml: max_retries: 2)
# succeeds against this same Overpass endpoint because core/fetcher.py
# wraps every provider call in retry-with-backoff -- a transient 504 from
# the shared public instance just gets retried. This script talked to
# Overpass directly with a single attempt and no retry at all, so the
# same transient 504s that `osm` shrugs off killed it outright. Matching
# that retry behavior here (same max_retries, same capped exponential
# backoff) is the fix.
_MAX_RETRIES = 2

_EARTH_RADIUS_M = 6_371_000.0


def _to_local_xy(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    """Flat-earth projection in meters, centered on (origin_lat,
    origin_lon) -- fine at the <=~60km scale this field ever needs (see
    module docstring)."""
    lat_rad = math.radians(origin_lat)
    x = math.radians(lon - origin_lon) * math.cos(lat_rad) * _EARTH_RADIUS_M
    y = math.radians(lat - origin_lat) * _EARTH_RADIUS_M
    return x, y


def _point_to_segment_distance_m(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Shortest distance from point (px,py) to segment [(ax,ay),(bx,by)],
    all already in local meters. Standard clamped-projection formula."""
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    nearest_x, nearest_y = ax + t * dx, ay + t * dy
    return math.hypot(px - nearest_x, py - nearest_y)


def _min_distance_to_ways_m(center_lat: float, center_lon: float, ways: list[list[tuple[float, float]]]) -> Optional[float]:
    """`ways` is a list of node-coordinate lists (lat, lon), one per
    OSM way. Returns the minimum point-to-segment distance across every
    segment of every way, in meters, or None if `ways` is empty."""
    best: Optional[float] = None
    for nodes in ways:
        points = [_to_local_xy(lat, lon, center_lat, center_lon) for lat, lon in nodes]
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            d = _point_to_segment_distance_m(0.0, 0.0, ax, ay, bx, by)
            if best is None or d < best:
                best = d
    return best


def _load_zones(zone_ids: Optional[list]):
    from zones import list_zones

    registry = {z.zone_id: z for z in list_zones()}
    if zone_ids is None:
        return list(registry.values())
    unknown = [zid for zid in zone_ids if zid not in registry]
    if unknown:
        raise ValueError(f"Unknown zone_id(s): {unknown}. Known zones: {sorted(registry)}")
    return [registry[zid] for zid in zone_ids]


async def _fetch_one_zone(session, zone) -> Optional[float]:
    """One Overpass query for waterway=river|stream ways in the padded
    bbox, then the min point-to-segment distance from the zone's center
    to any of them. Returns None (not an exception) on any network/parse
    failure or a genuine "nothing mapped nearby" result -- both degrade
    to "no override this run", matching every other fetcher in this
    package."""
    south = zone.min_lat - _SEARCH_PAD_DEG
    west = zone.min_lon - _SEARCH_PAD_DEG
    north = zone.max_lat + _SEARCH_PAD_DEG
    east = zone.max_lon + _SEARCH_PAD_DEG
    query = f"""
    [out:json][timeout:25];
    way["waterway"~"^(river|stream)$"]({south},{west},{north},{east});
    out geom;
    """
    headers = {"User-Agent": "hazard-platform-sih2026/1.0 (SIH26191 Rescue Arc)"}
    data = None
    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with session.post(OVERPASS_URL, data={"data": query}, headers=headers, timeout=30) as resp:
                resp.raise_for_status()
                data = await resp.json()
            break
        except Exception as exc:  # noqa: BLE001 - network/parse errors -> retry, then no override
            last_error = exc
            backoff = min(2 ** attempt, 15)
            if attempt < _MAX_RETRIES:
                print(f"fetch_river_distance: {zone.zone_id} attempt {attempt}/{_MAX_RETRIES} failed: {exc} (retrying in {backoff}s)")
                await asyncio.sleep(backoff)
    if data is None:
        print(f"fetch_river_distance: {zone.zone_id} failed after {_MAX_RETRIES} attempts: {last_error}")
        return None

    ways = []
    for el in data.get("elements", []):
        geometry = el.get("geometry")
        if not geometry:
            continue
        ways.append([(pt["lat"], pt["lon"]) for pt in geometry if "lat" in pt and "lon" in pt])
    if not ways:
        return None

    center_lat = (zone.min_lat + zone.max_lat) / 2
    center_lon = (zone.min_lon + zone.max_lon) / 2
    return _min_distance_to_ways_m(center_lat, center_lon, ways)


async def _gather(zones: list) -> dict:
    import aiohttp

    results = {}
    async with aiohttp.ClientSession() as session:
        for i, zone in enumerate(zones):
            if i > 0:
                # Politeness delay, same reasoning as osm_vector.py's
                # min_interval_seconds -- this is a shared public
                # instance, and batches here can cover every zone.
                await asyncio.sleep(_OVERPASS_MIN_INTERVAL_S)
            results[zone.zone_id] = await _fetch_one_zone(session, zone)
    return results


def fetch_river_distances_for_zones(zone_ids: Optional[list] = None) -> dict:
    """Returns {zone_id: meters_or_None}."""
    zones = _load_zones(zone_ids)
    return asyncio.run(_gather(zones))


def ingest_river_distances(zone_ids: Optional[list] = None, store: Optional[StaticDatasetStore] = None) -> dict:
    """auto_refresh.py's entry point."""
    store = store or StaticDatasetStore()
    results = fetch_river_distances_for_zones(zone_ids)
    for zone_id, value in results.items():
        if value is None:
            continue
        store.upsert(zone_id, FIELD_NAME, float(value), "OSM_Overpass_waterway_auto")
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zone-ids", nargs="*", default=None, help="Restrict to these zone_id(s); default: every zone in zones.py")
    parser.add_argument("--db", default="static_zone_data.db")
    args = parser.parse_args(argv)

    store = StaticDatasetStore(db_path=args.db)
    results = ingest_river_distances(args.zone_ids, store)
    for zone_id, value in results.items():
        if value is None:
            print(f"{zone_id}: no OSM-mapped river/stream within {_SEARCH_PAD_DEG}deg -- not overridden")
        else:
            print(f"{zone_id}: distance_to_river_m={value:.0f} (OSM Overpass)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
