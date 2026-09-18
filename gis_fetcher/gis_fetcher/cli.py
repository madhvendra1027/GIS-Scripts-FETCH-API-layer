"""
Command-line entry point.

    python -m gis_fetcher.cli \\
        --providers weather earthquakes \\
        --bbox -122.6 37.6 -122.3 37.9 \\
        --out sf_bay.geojson --verbose

Run `python -m gis_fetcher.cli --help` for all options. If you installed
the package (`pip install -e .`), the same thing is available as the
`gis-fetch` command.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import providers  # noqa: F401  (import triggers provider registration)
from .core.base import BBox
from .core.cache import Cache
from .core.config import load_config
from .core.fetcher import GISDataFetcher
from .core.registry import available_providers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gis-fetch",
        description="Fetch live geospatial data from multiple public APIs and merge it into one GeoJSON file.",
    )
    parser.add_argument(
        "--providers",
        nargs="+",
        required=True,
        help=f"Space-separated provider names to query. Available: {available_providers()}",
    )
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        required=True,
        help="Bounding box in WGS84 lon/lat, e.g. --bbox -122.6 37.6 -122.3 37.9",
    )
    parser.add_argument("--out", default="output.geojson", help="Output GeoJSON path")
    parser.add_argument("--config", default="config/providers.yaml", help="Path to provider config YAML")
    parser.add_argument("--cache", action="store_true", help="Enable local disk caching of responses")
    parser.add_argument("--verbose", action="store_true", help="Verbose (INFO-level) logging")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    bbox = BBox(*args.bbox)
    config = load_config(args.config)
    cache = Cache(enabled=args.cache)
    fetcher = GISDataFetcher(config=config, cache=cache)

    results = fetcher.fetch(args.providers, bbox)

    merged = {"type": "FeatureCollection", "features": []}
    exit_code = 0
    for result in results:
        if not result.ok:
            print(f"[warn] provider '{result.provider}' failed: {result.error}", file=sys.stderr)
            exit_code = 1
            continue
        merged["features"].extend(result.to_geojson()["features"])
        print(f"[ok]   provider '{result.provider}' returned {len(result.features)} feature(s)")

    with open(args.out, "w") as f:
        json.dump(merged, f, indent=2)

    print(f"Wrote {len(merged['features'])} total feature(s) to {args.out}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
