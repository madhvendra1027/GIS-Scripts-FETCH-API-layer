"""load_sediment_type.py — one-time ingestion of sediment_type_code
(EROSION) from CSIR-NIO / Geological Survey of India coastal geomorphology
maps.

SOURCE (free, but more manual than the other two -- no single national
downloadable layer like NCCR's shoreline atlas or GMW's mangrove data):
  Geological Survey of India (GSI) Bhukosh portal (coastal geomorphology
  / geo-environmental maps, state-wise):
    https://bhukosh.gsi.gov.in/
  CSIR-National Institute of Oceanography (coastal process studies,
  published papers with sediment classification, Goa HQ):
    https://www.nio.res.in/  ->  "Publications"
  Practical fallback many pilots use instead of chasing per-state GSI
  maps: derive a coarse sediment proxy from ISRIC SoilGrids' *coastal*
  grid cells (the same `soil` provider this repo already calls for
  LANDSLIDE) -- sandy vs. muddy/clayey coastal sediment correlates
  reasonably with SoilGrids' USDA texture class at the shoreline. This
  is a proxy, not a real substitute for GSI's coastal geomorphology
  classification -- label it as such in `source` if you go this route.

WHAT YOU GET: typically a PDF report or a static map image per coastal
stretch, sometimes a shapefile for specific states (Bhukosh varies by
state). Classification is usually qualitative (sandy / muddy / rocky /
mixed) -- map that to this project's existing numeric convention before
ingesting. This repo already defines a numeric soil-texture code in
gis_fetcher/providers/soil.py's `_TEXTURE_ORDER` (1=clay ... 12=sand);
reusing those same codes for coastal sediment keeps one convention
across LANDSLIDE and EROSION instead of inventing a second one:

    SEDIMENT_CODE = {"sandy": 12, "muddy": 1, "rocky": 7, "mixed": 9}

HOW TO TURN IT INTO A CSV: manual for this one, realistically --
build a small lookup table by hand from GSI's report for your zones
(zone_id, value) where value is the numeric code above, then run this
script. There typically aren't enough coastal zones in a pilot for this
to be a large amount of manual work.

THEN RUN:
    python3 -m data_pipeline.static_datasets.load_sediment_type \\
        sediment_type.csv --source "GSI_Bhukosh_coastal_geomorphology"
"""

from __future__ import annotations

import argparse
import sys

from .load_from_csv import load_csv
from .store import StaticDatasetStore

FIELD_NAME = "sediment_type_code"

# Shared with gis_fetcher/providers/soil.py's _TEXTURE_ORDER convention,
# repeated here (not imported) since this package doesn't depend on
# gis_fetcher -- see normalize.py's FeatureLike Protocol for the same
# "duck-typed, no hard dependency" pattern.
SEDIMENT_CODE_HINTS = {"clay": 1, "muddy": 1, "loam": 7, "mixed": 7, "rocky": 7, "sandy": 12, "sand": 12}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path", help="CSV with columns: zone_id,value (numeric code -- see SEDIMENT_CODE_HINTS above)")
    parser.add_argument("--source", default="GSI_Bhukosh_coastal_geomorphology", help="Dataset name + vintage")
    parser.add_argument("--db", default="static_zone_data.db")
    args = parser.parse_args(argv)

    store = StaticDatasetStore(db_path=args.db)
    summary = load_csv(args.csv_path, FIELD_NAME, args.source, store)
    print(f"sediment_type_code: loaded {len(summary['loaded'])} zone(s): {summary['loaded']}")
    if summary["skipped"]:
        print(f"  skipped: {summary['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
