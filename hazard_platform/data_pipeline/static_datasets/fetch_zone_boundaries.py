"""fetch_zone_boundaries.py — optional CLI over `zone_boundaries.get_boundaries()`.

You do NOT need to run this before `spatial_join.py` -- that script now
calls `get_boundaries()` itself and fetches+caches whatever's missing
automatically (see `zone_boundaries.py`'s docstring for why this
specific step, unlike the dataset downloads in STATIC_DATASETS.md, is
safe to fully automate: it's a live free API, not a portal download).

This CLI still exists for when a human wants to run it explicitly:
  - Pre-warming the cache before an offline/no-network demo.
  - Getting the "which zones OSM doesn't have a boundary for" report on
    its own, without also running a spatial join.
  - Bulk-fetching admin_level candidates for a large zone list.

USAGE:
    python3 -m data_pipeline.static_datasets.fetch_zone_boundaries

    # Restrict to specific zones, or pin an admin_level (OSM's
    # granularity code -- 8 is typically town/municipality in India,
    # but this varies by state; omit it and inspect the output first
    # if you're not sure):
    python3 -m data_pipeline.static_datasets.fetch_zone_boundaries \\
        --zones Z-ODISHA-PURI-01 Z-KERALA-WAYANAD-01 --admin-level 8

Requires `gis_fetcher` importable alongside this package (same
requirement `pipeline_runner.py`'s live fetches already have).
"""

from __future__ import annotations

import argparse
import sys

from .zone_boundaries import DEFAULT_CACHE_PATH, get_boundaries


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zones", nargs="*", default=None, help="zone_id(s) to fetch; default: all zones in zones.py")
    parser.add_argument("--admin-level", default=None, help="OSM admin_level to pin, e.g. '8'. Omit to accept any match.")
    parser.add_argument("--bbox-pad-deg", type=float, default=0.15, help="Search-area padding around each zone's demo bbox, in degrees (default 0.15 ~ 15km).")
    parser.add_argument("--cache", default=DEFAULT_CACHE_PATH, help=f"Cache file path (default {DEFAULT_CACHE_PATH})")
    parser.add_argument("--force-refetch", action="store_true", help="Re-query OSM even for zone_ids already in the cache (including ones previously not found)")
    args = parser.parse_args(argv)

    try:
        results = get_boundaries(
            zone_ids=args.zones,
            admin_level=args.admin_level,
            bbox_pad_deg=args.bbox_pad_deg,
            cache_path=args.cache,
            force_refetch=args.force_refetch,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    found = [zid for zid, feat in results.items() if feat is not None]
    empty = [zid for zid, feat in results.items() if feat is None]
    print(f"{len(found)} zone(s) with a real OSM boundary, cached in {args.cache}: {found}")
    if empty:
        print(f"No OSM boundary found for (fall back to a hand-drawn polygon in your --zones file for these): {empty}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
