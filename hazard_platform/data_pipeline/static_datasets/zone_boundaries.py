"""zone_boundaries.py — the actual boundary-fetching + caching logic
behind `fetch_zone_boundaries.py`'s CLI, factored out so other code
(`spatial_join.py`) can call it directly instead of requiring a human to
run a separate command first.

Why this one *can* be fully automatic where the dataset downloads in
STATIC_DATASETS.md can't: getting a zone polygon from OpenStreetMap is a
live API call (Overpass), not a portal click-through. There's no file
to download, no login, no dataset that only exists as a PDF -- so there
is no step here that a person has to do that code can't. The only
reason to cache the result at all is politeness: Overpass is a shared
free community resource, and re-asking it for the same 5-zone boundary
on every pipeline run would be rude for no benefit, since these
boundaries don't change. That's the same reasoning `hazard_map.py`
already uses for 90-day TTLs on soil/slope/elevation.

`get_boundaries()` is the one function everything else should call:
    - Reads whatever is already on disk in the cache file.
    - Fetches (live, via `gis_fetcher`) only the zone_ids missing from
      the cache -- including zone_ids that were tried before and came
      back empty, unless `force_refetch=True`, so a place OSM genuinely
      doesn't have isn't re-queried forever.
    - Writes the merged result back to the cache file.
    - Returns {zone_id: geojson_Feature_dict_or_None}.

No human action is required for this to work the first time a zone is
needed; `fetch_zone_boundaries.py`'s CLI still exists on top of this for
the times a human *wants* one explicitly -- pre-warming the cache before
an offline demo, or getting the "which zones aren't in OSM" report
without also running a join.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

DEFAULT_CACHE_PATH = "zone_boundaries_cache.geojson"


def _load_zones_registry():
    # See fetch_zone_boundaries.py's old note: zones.py lives at the repo
    # root and this repo runs with PYTHONPATH=. rather than being pip
    # installed, so this is a plain top-level import.
    from zones import list_zones

    return list_zones()


def _read_cache(cache_path: str) -> dict:
    path = Path(cache_path)
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    cache = {feat["properties"]["zone_id"]: feat for feat in data.get("features", [])}
    for zone_id in data.get("_not_found_in_osm", []):
        cache[zone_id] = None  # tombstone -- do not re-fetch on future calls
    return cache


def _write_cache(cache_path: str, cache: dict) -> None:
    # A zone that was queried and genuinely has no OSM match is stored
    # as an explicit tombstone rather than just left out, so it isn't
    # silently re-fetched (and silently missing) on every future call.
    features = [feat for feat in cache.values() if feat is not None]
    tombstones = [zone_id for zone_id, feat in cache.items() if feat is None]
    payload = {
        "type": "FeatureCollection",
        "features": features,
        "_not_found_in_osm": tombstones,
    }
    with open(cache_path, "w") as f:
        json.dump(payload, f)


async def _fetch_live(zones: list, admin_level: Optional[str], bbox_pad_deg: float) -> dict:
    from gis_fetcher.core.base import BBox
    from gis_fetcher.core.fetcher import GISDataFetcher

    fetcher = GISDataFetcher()
    fetched = {}
    for zone in zones:
        bbox = BBox(
            min_lon=zone.min_lon - bbox_pad_deg,
            min_lat=zone.min_lat - bbox_pad_deg,
            max_lon=zone.max_lon + bbox_pad_deg,
            max_lat=zone.max_lat + bbox_pad_deg,
        )
        place_name = zone.name.split(",")[0].strip()  # "Puri, Odisha" -> "Puri"
        params = {"place_name": place_name}
        if admin_level:
            params["admin_level"] = admin_level
        (result,) = await fetcher.fetch_many(["admin_boundary"], bbox, params)
        if result.ok and result.features:
            feat = result.features[0].to_geojson_feature()  # most-granular match first
            feat["properties"]["zone_id"] = zone.zone_id
            fetched[zone.zone_id] = feat
        else:
            fetched[zone.zone_id] = None  # tombstone -- see _write_cache
    return fetched


def get_boundaries(
    zone_ids: Optional[list] = None,
    admin_level: Optional[str] = None,
    bbox_pad_deg: float = 0.15,
    cache_path: str = DEFAULT_CACHE_PATH,
    force_refetch: bool = False,
) -> dict:
    """Returns {zone_id: geojson Feature dict, or None if no OSM match
    exists}. Fetches live + updates the on-disk cache only for zone_ids
    not already cached (or all requested zone_ids if force_refetch).
    `zone_ids=None` means every zone in `zones.py`'s registry.
    """
    registry = {z.zone_id: z for z in _load_zones_registry()}
    wanted_ids = list(zone_ids) if zone_ids is not None else list(registry.keys())
    unknown = [zid for zid in wanted_ids if zid not in registry]
    if unknown:
        raise ValueError(f"Unknown zone_id(s): {unknown}. Known zones: {sorted(registry)}")

    cache = _read_cache(cache_path)
    if force_refetch:
        to_fetch_ids = wanted_ids
    else:
        to_fetch_ids = [zid for zid in wanted_ids if zid not in cache]

    if to_fetch_ids:
        zones_to_fetch = [registry[zid] for zid in to_fetch_ids]
        newly_fetched = asyncio.run(_fetch_live(zones_to_fetch, admin_level, bbox_pad_deg))
        cache.update(newly_fetched)
        _write_cache(cache_path, cache)

    return {zid: cache.get(zid) for zid in wanted_ids}
