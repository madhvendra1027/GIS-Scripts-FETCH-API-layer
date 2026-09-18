"""load_from_csv.py — the one generic ingestion routine every specific
loader script (load_shoreline_change.py, load_mangrove_cover.py, etc.)
wraps. Takes a simple two-column CSV (zone_id,value) and writes it into
StaticDatasetStore.

Why CSV and not "point at the shapefile directly": every source in
STATIC_DATASETS.md ships as a shapefile/GeoJSON/raster keyed by
geographic geometry (a transect, a coastal segment, a raster cell), not
by this project's zone_id. Turning "geometry -> value" into "zone_id ->
value" is a one-time spatial join against wherever your real zone
boundaries live (a shapefile of ward/panchayat/circle polygons -- this
repo's zones.py only has 5 demo bboxes, not real administrative
boundaries). That join is a GIS step this script deliberately does NOT
do for you, because doing it silently/approximately would be worse than
making you do it once, explicitly, with a tool that shows you the
result (QGIS, or geopandas' `gpd.sjoin`).

Recommended one-time workflow per dataset:
  1. Download the source file (see each specific loader's docstring for
     the exact URL).
  2. Open it in QGIS (free) alongside your real zone-boundary shapefile,
     or in Python with geopandas:

         import geopandas as gpd
         zones = gpd.read_file("your_zone_boundaries.shp")
         source = gpd.read_file("downloaded_dataset.shp")
         joined = gpd.sjoin(zones, source, how="left", predicate="intersects")
         joined[["zone_id", "value_column"]].to_csv("shoreline_change.csv", index=False)

  3. Run the matching load_*.py script against that CSV.

This keeps the spatial-join step visible and inspectable instead of
guessing which polygon a zone belongs to.
"""

from __future__ import annotations

import argparse
import csv
import sys

from .store import StaticDatasetStore


def load_csv(
    csv_path: str,
    field_name: str,
    source_label: str,
    store: StaticDatasetStore,
    zone_id_column: str = "zone_id",
    value_column: str = "value",
) -> dict:
    """Reads `csv_path` and upserts one row per zone into `store` for
    `field_name`. Returns a summary dict for printing -- how many rows
    loaded, skipped (blank/non-numeric value), and which zone_ids were
    touched, so you can sanity-check a run before trusting it.
    """
    loaded, skipped = [], []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        missing_cols = {zone_id_column, value_column} - set(reader.fieldnames or [])
        if missing_cols:
            raise ValueError(
                f"{csv_path} is missing column(s) {sorted(missing_cols)}. "
                f"Found columns: {reader.fieldnames}"
            )
        for row in reader:
            zone_id = (row.get(zone_id_column) or "").strip()
            raw_value = (row.get(value_column) or "").strip()
            if not zone_id:
                continue
            try:
                value = float(raw_value) if raw_value else None
            except ValueError:
                skipped.append((zone_id, raw_value))
                continue
            store.upsert(zone_id, field_name, value, source_label)
            loaded.append(zone_id)

    return {"field_name": field_name, "loaded": loaded, "skipped": skipped}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generic loader: CSV(zone_id,value) -> StaticDatasetStore."
    )
    parser.add_argument("csv_path")
    parser.add_argument("field_name", help="e.g. shoreline_change_rate_m_per_yr")
    parser.add_argument("--source", required=True, help="e.g. ISRO_NCCR_shoreline_atlas_2023")
    parser.add_argument("--db", default="static_zone_data.db")
    parser.add_argument("--zone-column", default="zone_id")
    parser.add_argument("--value-column", default="value")
    args = parser.parse_args(argv)

    store = StaticDatasetStore(db_path=args.db)
    summary = load_csv(
        args.csv_path, args.field_name, args.source, store,
        zone_id_column=args.zone_column, value_column=args.value_column,
    )
    print(f"Loaded {len(summary['loaded'])} zone(s) for '{summary['field_name']}': {summary['loaded']}")
    if summary["skipped"]:
        print(f"Skipped {len(summary['skipped'])} row(s) with non-numeric value: {summary['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
