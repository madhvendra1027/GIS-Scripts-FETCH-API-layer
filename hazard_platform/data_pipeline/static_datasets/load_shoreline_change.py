"""load_shoreline_change.py — one-time ingestion of shoreline_change_rate_m_per_yr
(EROSION) from the ISRO/NCCR National Shoreline Change Assessment atlas.

SOURCE (free, no login required beyond a Bhuvan account):
  ISRO Bhuvan Coastal & Marine thematic services:
    https://bhuvan.nrsc.gov.in/  ->  "Thematic Services" -> "Coastal &
    Marine" -> "Shoreline Change" layer. NCCR (National Centre for
    Coastal Research, under MoES) publishes the underlying shapefiles at:
    https://www.nccr.gov.in/  ->  "Data & Publications" -> shoreline
    change assessment reports (state-wise, ~1km transect resolution,
    covers most of the Indian mainland coast).
  Alternative: the shapefiles are also mirrored via India's National
  Data Sharing and Accessibility Policy portal:
    https://vedas.sac.gov.in/ and https://data.gov.in (search "shoreline
    change" -- listings move periodically, search rather than relying on
    a fixed deep link).

WHAT YOU GET: a shapefile of coastal transects, each carrying an
erosion/accretion rate in m/yr (positive = accretion, negative = erosion
in NCCR's convention -- flip the sign if you want "positive = eroding"
to match this project's risk-increasing convention before ingesting).

HOW TO TURN IT INTO A CSV THIS SCRIPT CAN READ (see load_from_csv.py's
docstring for the general pattern):

    import geopandas as gpd
    zones = gpd.read_file("your_zone_boundaries.shp")   # your real zone polygons
    transects = gpd.read_file("nccr_shoreline_transects.shp")
    joined = gpd.sjoin(zones, transects, how="left", predicate="intersects")
    # NCCR's rate column name varies by state file -- inspect with
    # transects.columns first; commonly "EPR_myr" or similar.
    joined.groupby("zone_id")["EPR_myr"].mean().mul(-1).reset_index() \\
        .rename(columns={"EPR_myr": "value"}) \\
        .to_csv("shoreline_change.csv", index=False)

THEN RUN:
    python3 -m data_pipeline.static_datasets.load_shoreline_change \\
        shoreline_change.csv --source "ISRO_NCCR_shoreline_atlas_2023"
"""

from __future__ import annotations

import argparse
import sys

from .load_from_csv import load_csv
from .store import StaticDatasetStore

FIELD_NAME = "shoreline_change_rate_m_per_yr"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path", help="CSV with columns: zone_id,value")
    parser.add_argument("--source", default="ISRO_NCCR_shoreline_atlas", help="Dataset name + vintage, e.g. ISRO_NCCR_shoreline_atlas_2023")
    parser.add_argument("--db", default="static_zone_data.db")
    args = parser.parse_args(argv)

    store = StaticDatasetStore(db_path=args.db)
    summary = load_csv(args.csv_path, FIELD_NAME, args.source, store)
    print(f"shoreline_change_rate_m_per_yr: loaded {len(summary['loaded'])} zone(s): {summary['loaded']}")
    if summary["skipped"]:
        print(f"  skipped: {summary['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
