"""ingest_grid_csv.py — turn an already-downloaded point-grid CSV
(latitude, longitude, <value column>) into zone_id,value and load it
straight into StaticDatasetStore, in one command, with no geopandas
dependency.

WHY THIS EXISTS, AND WHY IT'S SEPARATE FROM ingest_dataset.py:
ingest_dataset.py + spatial_join.py were built for NCCR/GMW shipping as
a shapefile of transects/polygons that needs a real geometric overlay
against zone boundaries. The files actually downloaded here are a
different, simpler shape: a regular ~0.125deg map-sheet grid, one row
per cell, with latitude/longitude as plain columns and the target value
already computed per cell under the *exact* field names this project
uses (mangrove_cover_pct, shoreline_change_rate_m_per_yr). There is no
polygon to overlay -- just "which zone is this point closest to" -- so
requiring geopandas + a shapefile join here would be adding a dependency
for a problem that doesn't need one. This script is pure stdlib (csv,
math), matching the "no unnecessary GIS dependency" convention
fetch_river_distance.py and fetch_historical_events.py already use.

MATCHING METHOD: for each zone (bbox from zones.py), first collect every
grid point whose (lat, lon) falls inside that zone's own bbox and
average them (mirrors join_mean_value's "mean over everything
intersecting" behavior in spatial_join.py). India's coastal grid here
is spaced ~0.125deg (~14km) while every seeded zone bbox is only
~0.05deg (~5.5km) wide, so a zone very often contains zero grid points
outright -- when that happens this falls back to the single NEAREST
grid point to the zone's bbox center, within --max-distance-deg (default
1.0deg, ~110km). A nearest-neighbour match is written with
"_nearest_gridcell" appended to `source` so nobody downstream mistakes
a snap-to-nearest-cell for an exact intersection -- same "label the
proxy as a proxy" discipline fetch_sediment_type.py uses for its
"_proxy_auto" suffix. A zone with no grid point at all within
--max-distance-deg (e.g. Patna/Joshimath/Guwahati, which are inland and
genuinely outside this coastal grid's coverage) returns None -- not a
guess -- and is simply left un-upserted, same "no override this run
rather than fabricate" rule every other fetcher in this package follows.

SIGN CONVENTION: NCCR's shoreline product is accretion-positive (see
load_shoreline_change.py / ingest_dataset.py's docstrings); this
project's scoring convention is erosion-positive (higher = more
hazard). This script negates shoreline_change_rate_m_per_yr by default
-- checked against this specific downloaded file: cells with more
erosion length than accretion length do carry a negative rate, and
cells with net accretion carry a positive one, confirming it follows
NCCR's usual convention -- pass --no-negate if a future re-download
ever ships already-flipped. mangrove_cover_pct is never negated.

THEN RUN (once per downloaded dataset vintage -- re-run only when
NCCR/GMW publish a newer one; nothing here needs re-running on a normal
pipeline_runner.py invocation, same as ingest_dataset.py):

    python3 -m data_pipeline.static_datasets.ingest_grid_csv \\
        --field-name mangrove_cover_pct \\
        --file mangrove_cover_pct.csv \\
        --source GlobalMangroveWatch_2020

    python3 -m data_pipeline.static_datasets.ingest_grid_csv \\
        --field-name shoreline_change_rate_m_per_yr \\
        --file shoreline_change_rate_m_per_yr.csv \\
        --source ISRO_NCCR_shoreline_atlas_2023
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from typing import Optional

from .store import StaticDatasetStore

# Only shoreline needs the accretion-positive -> erosion-positive flip;
# mangrove_cover_pct is a plain percentage in both source and target.
_NEGATE_BY_DEFAULT = {"shoreline_change_rate_m_per_yr": True}


def _load_zones(zone_ids: Optional[list]):
    from zones import list_zones

    registry = {z.zone_id: z for z in list_zones()}
    if zone_ids is None:
        return list(registry.values())
    unknown = [zid for zid in zone_ids if zid not in registry]
    if unknown:
        raise ValueError(f"Unknown zone_id(s): {unknown}. Known zones: {sorted(registry)}")
    return [registry[zid] for zid in zone_ids]


def _read_grid_points(csv_path: str, value_column: str) -> list[tuple[float, float, float]]:
    """Returns [(lat, lon, value), ...], silently skipping rows with a
    blank/non-numeric value (a real gap in the source grid, not
    something to interpolate or guess at)."""
    points = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        missing = {"latitude", "longitude", value_column} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{csv_path} is missing column(s) {sorted(missing)}. Found columns: {reader.fieldnames}")
        for row in reader:
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
                value = float(row[value_column])
            except (TypeError, ValueError):
                continue
            points.append((lat, lon, value))
    return points


def _points_in_bbox(zone, points: list) -> list[float]:
    return [v for lat, lon, v in points if zone.min_lat <= lat <= zone.max_lat and zone.min_lon <= lon <= zone.max_lon]


def _nearest_point(zone, points: list, max_distance_deg: float) -> Optional[tuple[float, float]]:
    """(value, distance_deg) for the single closest grid point to the
    zone's bbox center, or None if nothing is within max_distance_deg.
    Plain Euclidean distance in degrees -- fine at this ~0.125deg grid
    spacing, same "flat-earth is accurate enough at this scale"
    reasoning fetch_river_distance.py uses for its own (finer-scale)
    projection."""
    center_lat = (zone.min_lat + zone.max_lat) / 2
    center_lon = (zone.min_lon + zone.max_lon) / 2
    best = None
    for lat, lon, v in points:
        d = math.hypot(lat - center_lat, lon - center_lon)
        if d > max_distance_deg:
            continue
        if best is None or d < best[1]:
            best = (v, d)
    return best


def match_grid_to_zones(
    csv_path: str,
    value_column: str,
    zone_ids: Optional[list] = None,
    max_distance_deg: float = 1.0,
    negate: bool = False,
) -> dict:
    """Returns {zone_id: {"value": float_or_None, "method": "bbox_mean"|"nearest_gridcell"|"none"}}."""
    zones = _load_zones(zone_ids)
    points = _read_grid_points(csv_path, value_column)

    results = {}
    for zone in zones:
        in_bbox = _points_in_bbox(zone, points)
        if in_bbox:
            value, method = sum(in_bbox) / len(in_bbox), "bbox_mean"
        else:
            nearest = _nearest_point(zone, points, max_distance_deg)
            value, method = (nearest[0], "nearest_gridcell") if nearest else (None, "none")
        if value is not None and negate:
            value = -value
        results[zone.zone_id] = {"value": value, "method": method}
    return results


def ingest_grid_csv(
    csv_path: str,
    field_name: str,
    source: str,
    store: Optional[StaticDatasetStore] = None,
    zone_ids: Optional[list] = None,
    max_distance_deg: float = 1.0,
    negate: Optional[bool] = None,
) -> dict:
    """Programmatic entry point (other code -- or a future auto_refresh.py
    step, if this project ever wants to poll a *live* grid source -- can
    call this directly, same shape as every ingest_* helper elsewhere in
    this package)."""
    store = store or StaticDatasetStore()
    if negate is None:
        negate = _NEGATE_BY_DEFAULT.get(field_name, False)
    results = match_grid_to_zones(csv_path, field_name, zone_ids, max_distance_deg, negate)
    for zone_id, info in results.items():
        if info["value"] is None:
            continue
        source_label = source if info["method"] == "bbox_mean" else f"{source}_nearest_gridcell"
        store.upsert(zone_id, field_name, float(info["value"]), source_label)
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--field-name", required=True, choices=["shoreline_change_rate_m_per_yr", "mangrove_cover_pct"])
    parser.add_argument("--file", required=True, dest="csv_path", help="Downloaded grid-cell CSV (latitude,longitude,<field-name>,...)")
    parser.add_argument("--source", required=True, help="Dataset name + vintage, e.g. ISRO_NCCR_shoreline_atlas_2023")
    parser.add_argument("--zone-ids", nargs="*", default=None, help="Restrict to these zone_id(s); default: every zone in zones.py")
    parser.add_argument("--max-distance-deg", type=float, default=1.0, help="Max nearest-neighbour search radius in degrees when no grid point falls inside a zone's bbox (default 1.0 ~ 110km)")
    parser.add_argument("--no-negate", action="store_true", help="Don't flip sign (shoreline only; default negates NCCR's accretion-positive convention to this project's erosion-positive convention)")
    parser.add_argument("--db", default="static_zone_data.db")
    args = parser.parse_args(argv)

    negate = _NEGATE_BY_DEFAULT.get(args.field_name, False) and not args.no_negate

    store = StaticDatasetStore(db_path=args.db)
    results = ingest_grid_csv(
        args.csv_path, args.field_name, args.source, store,
        zone_ids=args.zone_ids, max_distance_deg=args.max_distance_deg, negate=negate,
    )
    for zone_id, info in sorted(results.items()):
        if info["value"] is None:
            print(f"{zone_id}: no grid point within {args.max_distance_deg}deg -- not overridden (likely outside this dataset's coastal coverage)")
        else:
            print(f"{zone_id}: {args.field_name}={info['value']:.4f} ({info['method']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
