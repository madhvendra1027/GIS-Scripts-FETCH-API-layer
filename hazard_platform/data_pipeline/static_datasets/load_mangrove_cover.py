"""load_mangrove_cover.py — one-time (re-run yearly) ingestion of
mangrove_cover_pct (EROSION, protective factor) from Global Mangrove Watch.

SOURCE (free, no login):
  Global Mangrove Watch, via the Ocean Data Viewer:
    https://data.unep-wcmc.org/datasets/45  ("Global Mangrove Watch
    Mangrove Cover" -- direct shapefile/GeoJSON download, annual extent)
  Interactive viewer (to preview before downloading):
    https://www.globalmangrovewatch.org/
  Alternative mirror (Global Forest Watch, same underlying GMW data):
    https://data.globalforestwatch.org/  -> search "mangrove"

WHAT YOU GET: polygons of mangrove extent for a given year. Re-download
annually -- extent genuinely changes year to year (restoration projects,
clearing), unlike shoreline-change-rate or sediment type which are
closer to fixed.

HOW TO TURN IT INTO A CSV:

    import geopandas as gpd
    zones = gpd.read_file("your_zone_boundaries.shp")
    mangroves = gpd.read_file("gmw_v3_2020_your_region.shp")
    # percent of each zone's area covered by mangrove polygons
    zones = zones.to_crs(mangroves.crs)
    overlay = gpd.overlay(zones, mangroves, how="intersection")
    overlay["area_m2"] = overlay.to_crs(epsg=3857).geometry.area
    zones["zone_area_m2"] = zones.to_crs(epsg=3857).geometry.area
    pct = (overlay.groupby("zone_id")["area_m2"].sum()
           / zones.set_index("zone_id")["zone_area_m2"] * 100).clip(upper=100)
    pct.reset_index().rename(columns={"area_m2": "value"}).to_csv("mangrove_cover.csv", index=False)

    # Zones with zero mangrove overlap won't appear in `overlay` at all --
    # add them back in explicitly with value=0 (absence of mangrove is a
    # real, meaningful 0%, not a missing value) before writing the CSV.

THEN RUN:
    python3 -m data_pipeline.static_datasets.load_mangrove_cover \\
        mangrove_cover.csv --source "GlobalMangroveWatch_2020"
"""

from __future__ import annotations

import argparse
import sys

from .load_from_csv import load_csv
from .store import StaticDatasetStore

FIELD_NAME = "mangrove_cover_pct"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path", help="CSV with columns: zone_id,value")
    parser.add_argument("--source", default="GlobalMangroveWatch", help="Dataset name + vintage year, e.g. GlobalMangroveWatch_2020")
    parser.add_argument("--db", default="static_zone_data.db")
    args = parser.parse_args(argv)

    store = StaticDatasetStore(db_path=args.db)
    summary = load_csv(args.csv_path, FIELD_NAME, args.source, store)
    print(f"mangrove_cover_pct: loaded {len(summary['loaded'])} zone(s): {summary['loaded']}")
    if summary["skipped"]:
        print(f"  skipped: {summary['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
