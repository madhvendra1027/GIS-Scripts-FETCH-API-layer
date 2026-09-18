"""spatial_join.py — the one generic spatial-join routine that
load_shoreline_change.py, load_mangrove_cover.py, and
load_sediment_type.py's docstrings each showed as a one-off geopandas
snippet you were expected to copy, edit, and run by hand. This script
runs that same join as an actual command, so the "spatial join" part of
each dataset's manual work stops being "read a docstring and write
Python" and becomes "run one line with the right column names".

It does NOT remove the two steps that genuinely can't be automated
(see STATIC_DATASETS.md): downloading the source file in the first
place (no live API exists for these), and reviewing the join result
before trusting it (a silent/wrong join into a hazard platform is worse
than a slow correct one).

USAGE:
    # Zone polygons are fetched (and cached) automatically -- you don't
    # need to run fetch_zone_boundaries.py first:
    python3 -m data_pipeline.static_datasets.spatial_join \\
        --dataset nccr_shoreline_transects.shp \\
        --value-column EPR_myr \\
        --agg mean --negate \\
        --out shoreline_change.csv

    # Restrict to specific zones, or supply your own boundaries file
    # (e.g. a hand-drawn polygon for the rare place OSM doesn't have):
    python3 -m data_pipeline.static_datasets.spatial_join \\
        --zone-ids Z-ODISHA-PURI-01 Z-KERALA-WAYANAD-01 \\
        --dataset nccr_shoreline_transects.shp --value-column EPR_myr \\
        --out shoreline_change.csv

    python3 -m data_pipeline.static_datasets.spatial_join \\
        --zones-file my_hand_drawn_zones.geojson \\
        --dataset nccr_shoreline_transects.shp --value-column EPR_myr \\
        --out shoreline_change.csv

    # Mangrove cover as percent-of-zone-area (not a value column to
    # average -- the dataset itself IS the mangrove polygons, so this
    # computes area overlap instead):
    python3 -m data_pipeline.static_datasets.spatial_join \\
        --dataset gmw_v3_2020_your_region.shp \\
        --mode area_pct \\
        --out mangrove_cover.csv

By default this uses every zone in `zones.py`'s registry, fetching each
one's real boundary from OpenStreetMap on first use and caching it in
`zone_boundaries_cache.geojson` (see `zone_boundaries.py` for why that
fetch, unlike the dataset download below, is safe to do without asking
you first). `--zones-file` overrides this entirely with your own
GeoJSON/shapefile -- use it for a place OSM doesn't have, or if you
already maintain real administrative boundaries elsewhere.

Requires geopandas (not a dependency of the rest of this repo -- only
this one-time ingestion path needs it):
    pip install geopandas --break-system-packages
"""

from __future__ import annotations

import argparse
import csv
import sys


def _zones_gdf_from_file(path: str):
    import geopandas as gpd

    gdf = gpd.read_file(path)
    if "zone_id" not in gdf.columns:
        raise ValueError(
            f"{path} has no 'zone_id' column/property. Every zone feature must carry "
            "the same zone_id used elsewhere in this repo (see zones.py)."
        )
    return gdf


def _zones_gdf_auto(zone_ids, admin_level, cache_path, force_refetch):
    """Builds the zones GeoDataFrame from live-fetched + cached OSM
    boundaries (see zone_boundaries.py) instead of requiring a
    pre-built file. Zones with no OSM match are dropped with a warning
    printed to stderr -- spatial_join can't join against a polygon that
    doesn't exist; fall back to --zones-file for those specific zones.
    """
    import geopandas as gpd

    from .zone_boundaries import get_boundaries

    boundaries = get_boundaries(
        zone_ids=zone_ids, admin_level=admin_level, cache_path=cache_path, force_refetch=force_refetch
    )
    missing = [zid for zid, feat in boundaries.items() if feat is None]
    if missing:
        print(
            f"warning: no OSM boundary for {missing} -- excluded from this join; "
            "use --zones-file with a hand-drawn polygon for these if you need them included.",
            file=sys.stderr,
        )
    features = [feat for feat in boundaries.values() if feat is not None]
    if not features:
        raise ValueError("No zone boundaries available (OSM had none of the requested zones). Use --zones-file instead.")
    return gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")


def load_zones_gdf(zones_file, zone_ids, admin_level, cache_path, force_refetch):
    if zones_file:
        return _zones_gdf_from_file(zones_file)
    return _zones_gdf_auto(zone_ids, admin_level, cache_path, force_refetch)


def join_mean_value(zones: "gpd.GeoDataFrame", dataset_path: str, value_column: str, agg: str, negate: bool) -> dict:
    """`agg='mean'|'first'` over every dataset feature intersecting a
    zone. Use this for point/line/polygon datasets that carry a numeric
    attribute per feature (shoreline transect rate, a lookup-table
    sediment code, ...). Returns {zone_id: value}."""
    import geopandas as gpd

    dataset = gpd.read_file(dataset_path)
    if value_column not in dataset.columns:
        raise ValueError(
            f"{dataset_path} has no column {value_column!r}. Available columns: "
            f"{list(dataset.columns)}"
        )
    dataset = dataset.to_crs(zones.crs) if zones.crs else dataset
    joined = gpd.sjoin(zones[["zone_id", "geometry"]], dataset[[value_column, "geometry"]], how="left", predicate="intersects")

    if agg == "mean":
        grouped = joined.groupby("zone_id")[value_column].mean()
    elif agg == "first":
        grouped = joined.groupby("zone_id")[value_column].first()
    else:
        raise ValueError(f"Unknown --agg {agg!r}, expected 'mean' or 'first'")

    result = grouped.to_dict()
    if negate:
        result = {k: (None if v is None else -v) for k, v in result.items()}
    # Zones with no intersecting dataset feature at all won't appear in
    # `joined` -- surface them explicitly as None rather than silently
    # dropping the zone from the output CSV.
    for zone_id in zones["zone_id"]:
        result.setdefault(zone_id, None)
    return result


def join_area_pct(zones: "gpd.GeoDataFrame", dataset_path: str) -> dict:
    """What fraction of each zone's area is covered by the dataset's
    polygons. Use this for extent datasets (mangrove cover) where the
    dataset itself IS the thing being measured, not an attribute to
    average. Zones with zero overlap correctly come back as 0.0, not
    None -- absence of mangrove is a real, meaningful measurement."""
    import geopandas as gpd

    dataset = gpd.read_file(dataset_path)
    dataset = dataset.to_crs(zones.crs) if zones.crs else dataset

    overlay = gpd.overlay(zones[["zone_id", "geometry"]], dataset[["geometry"]], how="intersection")
    overlay_area = overlay.to_crs(epsg=3857).geometry.area.groupby(overlay["zone_id"]).sum()
    zone_area = zones.set_index("zone_id").to_crs(epsg=3857).geometry.area

    pct = (overlay_area / zone_area * 100).clip(upper=100)
    result = pct.to_dict()
    for zone_id in zones["zone_id"]:
        result.setdefault(zone_id, 0.0)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zones-file", default=None, help="Your own zone boundary GeoJSON/shapefile with a zone_id column. Default: auto-fetch from OpenStreetMap instead (see zone_boundaries.py).")
    parser.add_argument("--zone-ids", nargs="*", default=None, help="Restrict auto-fetch to these zone_id(s); default: every zone in zones.py. Ignored with --zones-file.")
    parser.add_argument("--admin-level", default=None, help="OSM admin_level to pin for auto-fetch, e.g. '8'. Ignored with --zones-file.")
    parser.add_argument("--boundary-cache", default="zone_boundaries_cache.geojson", help="Where auto-fetched boundaries are cached (default zone_boundaries_cache.geojson). Ignored with --zones-file.")
    parser.add_argument("--force-refetch-boundaries", action="store_true", help="Re-query OSM for boundaries even if already cached. Ignored with --zones-file.")
    parser.add_argument("--dataset", required=True, help="Downloaded source dataset (shapefile/GeoJSON)")
    parser.add_argument("--out", required=True, help="Output CSV path, ready for load_*.py")
    parser.add_argument("--mode", choices=["value", "area_pct"], default="value")
    parser.add_argument("--value-column", help="Required for --mode value: the dataset column to average per zone")
    parser.add_argument("--agg", choices=["mean", "first"], default="mean")
    parser.add_argument("--negate", action="store_true", help="Flip sign (e.g. NCCR's accretion-positive convention -> erosion-positive)")
    args = parser.parse_args(argv)

    try:
        import geopandas  # noqa: F401
    except ImportError:
        print("geopandas is required: pip install geopandas --break-system-packages", file=sys.stderr)
        return 1

    zones = load_zones_gdf(args.zones_file, args.zone_ids, args.admin_level, args.boundary_cache, args.force_refetch_boundaries)

    if args.mode == "value":
        if not args.value_column:
            parser.error("--value-column is required for --mode value")
        result = join_mean_value(zones, args.dataset, args.value_column, args.agg, args.negate)
    else:
        result = join_area_pct(zones, args.dataset)

    with open(args.out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["zone_id", "value"])
        for zone_id, value in sorted(result.items()):
            writer.writerow([zone_id, "" if value is None else value])

    n_missing = sum(1 for v in result.values() if v is None)
    print(f"Wrote {len(result)} zone(s) to {args.out} ({n_missing} with no intersecting feature -- review before ingesting)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
