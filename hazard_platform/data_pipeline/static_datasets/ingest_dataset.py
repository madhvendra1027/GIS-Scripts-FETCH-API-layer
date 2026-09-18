"""ingest_dataset.py — the one command that replaces "run spatial_join.py,
inspect the CSV, then run the matching load_*.py" with a single
invocation, for the two static fields that still genuinely require a
one-time human download (shoreline_change_rate_m_per_yr,
mangrove_cover_pct -- see STATIC_DATASETS.md on why no live API exists
for either). sediment_type_code and historical_*_count no longer need
this at all: fetch_sediment_type.py and fetch_historical_events.py
(via auto_refresh.py) fetch those live, with zero file to download.

WHAT THIS AUTOMATES ON TOP OF spatial_join.py:
  - Picks the right join mode (mean value vs. area-percent) from
    --dataset-type instead of you remembering which --mode a given
    field needs.
  - For shoreline (--dataset-type shoreline), auto-detects the value
    column by checking the downloaded file's columns against a list of
    names NCCR's per-state shapefiles are commonly published under
    (EPR_myr, EPR, LRR, LRR_myr, Rate, rate_m_yr, shoreline_rate) instead
    of requiring --value-column every time -- override with
    --value-column if your file uses something else (NCCR's naming is
    not perfectly standardized across states, which is exactly why this
    is a best-effort guess with a clear error if it can't find one, not
    a silent wrong guess).
  - Applies the accretion-positive -> erosion-positive sign flip
    automatically for shoreline (NCCR's convention; see
    load_shoreline_change.py) instead of requiring --negate.
  - Writes straight into StaticDatasetStore (via the same load_csv()
    every load_*.py script uses) AND leaves the intermediate CSV on
    disk next to your input file, so the "review before trusting it"
    step STATIC_DATASETS.md insists on is still just as easy -- open
    the CSV, same as before.

WHAT THIS DOES NOT AUTOMATE (by design, not oversight): downloading the
source file. See STATIC_DATASETS.md's "data to download once" table --
no engineering choice here changes that ISRO/NCCR and Global Mangrove
Watch are portal downloads, not queryable REST APIs.

USAGE:
    python3 -m data_pipeline.static_datasets.ingest_dataset \\
        --dataset-type shoreline \\
        --file nccr_shoreline_transects.shp \\
        --source "ISRO_NCCR_shoreline_atlas_2023"

    python3 -m data_pipeline.static_datasets.ingest_dataset \\
        --dataset-type mangrove \\
        --file gmw_v3_2020_your_region.shp \\
        --source "GlobalMangroveWatch_2020"

    # Zone selection / boundary flags from spatial_join.py all still work:
    python3 -m data_pipeline.static_datasets.ingest_dataset \\
        --dataset-type shoreline --file nccr_shoreline_transects.shp \\
        --source "ISRO_NCCR_shoreline_atlas_2023" \\
        --zone-ids Z-ODISHA-PURI-01 --value-column LRR_myr
"""

from __future__ import annotations

import argparse
import sys

from .load_from_csv import load_csv
from .spatial_join import join_area_pct, join_mean_value, load_zones_gdf
from .store import StaticDatasetStore

# Column names observed (or documented) across NCCR's per-state shoreline
# shapefiles. Best-effort, in priority order -- NCCR does not use one
# fixed schema nationwide, so this is a guess list, not a guarantee.
_SHORELINE_COLUMN_CANDIDATES = [
    "EPR_myr", "EPR", "LRR_myr", "LRR", "Rate", "rate_m_yr",
    "shoreline_rate", "erosion_rate", "SCE",
]

_DATASET_CONFIG = {
    "shoreline": {
        "field_name": "shoreline_change_rate_m_per_yr",
        "mode": "value",
        "negate": True,  # NCCR: accretion-positive -> this repo: erosion-positive
        "default_source": "ISRO_NCCR_shoreline_atlas",
    },
    "mangrove": {
        "field_name": "mangrove_cover_pct",
        "mode": "area_pct",
        "negate": False,
        "default_source": "GlobalMangroveWatch",
    },
}


def _autodetect_value_column(dataset_path: str) -> str:
    import geopandas as gpd

    columns = set(gpd.read_file(dataset_path).columns)
    for candidate in _SHORELINE_COLUMN_CANDIDATES:
        if candidate in columns:
            return candidate
    raise ValueError(
        f"Could not auto-detect the shoreline-rate column in {dataset_path}. "
        f"Tried {_SHORELINE_COLUMN_CANDIDATES}; available columns: {sorted(columns)}. "
        "Pass --value-column explicitly."
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset-type", required=True, choices=sorted(_DATASET_CONFIG), help="Which static field this download is for")
    parser.add_argument("--file", required=True, dest="dataset_path", help="Downloaded shapefile/GeoJSON (see STATIC_DATASETS.md for source URLs)")
    parser.add_argument("--source", default=None, help="Dataset name + vintage, e.g. ISRO_NCCR_shoreline_atlas_2023 (default: a generic label per --dataset-type)")
    parser.add_argument("--value-column", default=None, help="Override auto-detection (shoreline only; ignored for mangrove)")
    parser.add_argument("--zones-file", default=None, help="Your own zone boundary file; default: auto-fetch from OpenStreetMap")
    parser.add_argument("--zone-ids", nargs="*", default=None, help="Restrict to these zone_id(s); default: every zone in zones.py")
    parser.add_argument("--admin-level", default=None)
    parser.add_argument("--boundary-cache", default="zone_boundaries_cache.geojson")
    parser.add_argument("--force-refetch-boundaries", action="store_true")
    parser.add_argument("--out-csv", default=None, help="Where to leave the intermediate CSV for review (default: <dataset-type>_ingest.csv)")
    parser.add_argument("--db", default="static_zone_data.db")
    args = parser.parse_args(argv)

    try:
        import geopandas  # noqa: F401
    except ImportError:
        print("geopandas is required: pip install geopandas --break-system-packages", file=sys.stderr)
        return 1

    config = _DATASET_CONFIG[args.dataset_type]
    source = args.source or config["default_source"]
    out_csv = args.out_csv or f"{args.dataset_type}_ingest.csv"

    zones = load_zones_gdf(args.zones_file, args.zone_ids, args.admin_level, args.boundary_cache, args.force_refetch_boundaries)

    if config["mode"] == "value":
        value_column = args.value_column or _autodetect_value_column(args.dataset_path)
        result = join_mean_value(zones, args.dataset_path, value_column, agg="mean", negate=config["negate"])
        print(f"Using value column: {value_column}" + (" (auto-detected)" if not args.value_column else ""))
    else:
        result = join_area_pct(zones, args.dataset_path)

    import csv as _csv

    with open(out_csv, "w", newline="") as f:
        writer = _csv.writer(f)
        writer.writerow(["zone_id", "value"])
        for zone_id, value in sorted(result.items()):
            writer.writerow([zone_id, "" if value is None else value])

    n_missing = sum(1 for v in result.values() if v is None)
    print(f"Joined {len(result)} zone(s), wrote {out_csv} for review ({n_missing} with no intersecting feature).")

    store = StaticDatasetStore(db_path=args.db)
    summary = load_csv(out_csv, config["field_name"], source, store)
    print(f"Ingested {config['field_name']}: {len(summary['loaded'])} zone(s) -> {summary['loaded']}")
    if summary["skipped"]:
        print(f"  skipped (non-numeric): {summary['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
