"""load_historical_counts.py — one-time ingestion of PER-ZONE (not
country-level) historical_flood_count / historical_landslide_count /
historical_erosion_events / historical_cloudburst_count.

Why this exists at all: gis_fetcher's `historical_events` provider
(ReliefWeb, live, free) already fills these fields -- but only at
country granularity ("India has had N reported flood events"), which is
a weak signal for ranking individual zones against each other. This
script lets you override that country-level number with a real
per-district/per-zone count wherever you've sourced one, while leaving
ReliefWeb's live value as the fallback for zones you haven't covered yet
(pipeline_runner.py only applies a static override when one exists --
see its `_STATIC_OVERRIDE_FIELDS` merge step).

SOURCES, best to weakest for India-specific per-district precision:
  1. NDMA (National Disaster Management Authority) state/district
     disaster reports and SDMA (State Disaster Management Authority)
     portals -- e.g. https://ndma.gov.in/ -> "Knowledge Bank" / state
     SDMA sites (each state hosts its own; no single national API).
     PDF/manual-download, not an API.
  2. EM-DAT (https://public.emdat.be/) -- free after registration,
     event-level with admin1 (state) location, sometimes admin2
     (district). International scope, India coverage is decent but not
     exhaustive.
  3. IIT Bombay's searchable India flood-event archive (built from IMD
     records) -- reported via PreventionWeb coverage of the project;
     search "IIT Bombay flood inventory India" for the current hosting
     location, since research-group dataset URLs move more often than
     government portals.
  4. This repo's own `historical_events` (ReliefWeb) as a country-level
     floor when nothing better exists for a given zone/hazard -- already
     live, already wired, requires nothing further from you.

WHAT YOU GET: usually a spreadsheet or PDF table of events by
district/year. Aggregate to one count per zone_id per hazard yourself
(e.g. "how many distinct flood events touched this district since
2000") -- there's no universal script for this because every source's
table layout differs; a few lines of pandas per source is the realistic
approach.

Field names this script accepts (pass one per invocation):
    historical_flood_count | historical_landslide_count |
    historical_erosion_events | historical_cloudburst_count

THEN RUN (once per hazard you have a per-zone count for):
    python3 -m data_pipeline.static_datasets.load_historical_counts \\
        flood_counts.csv historical_flood_count --source "NDMA_SDMA_2024"
"""

from __future__ import annotations

import argparse
import sys

from .load_from_csv import load_csv
from .store import StaticDatasetStore

VALID_FIELDS = {
    "historical_flood_count",
    "historical_landslide_count",
    "historical_erosion_events",
    "historical_cloudburst_count",
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path", help="CSV with columns: zone_id,value")
    parser.add_argument("field_name", choices=sorted(VALID_FIELDS))
    parser.add_argument("--source", required=True, help="e.g. NDMA_SDMA_2024, EM-DAT_2024, IITB_flood_archive")
    parser.add_argument("--db", default="static_zone_data.db")
    args = parser.parse_args(argv)

    store = StaticDatasetStore(db_path=args.db)
    summary = load_csv(args.csv_path, args.field_name, args.source, store)
    print(f"{args.field_name}: loaded {len(summary['loaded'])} zone(s): {summary['loaded']}")
    if summary["skipped"]:
        print(f"  skipped: {summary['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
