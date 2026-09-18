"""
Programmatic usage example (as opposed to the CLI). Run from the
project root:

    python examples/quickstart.py

This fetches live weather + earthquake data for a bounding box around
the San Francisco Bay Area and prints a summary. Requires outbound
internet access to api.open-meteo.com and earthquake.usgs.gov.
"""

from gis_fetcher import providers  # noqa: F401 - triggers registration
from gis_fetcher.core.base import BBox
from gis_fetcher.core.cache import Cache
from gis_fetcher.core.config import load_config
from gis_fetcher.core.fetcher import GISDataFetcher


def main():
    bbox = BBox(min_lon=-122.6, min_lat=37.6, max_lon=-122.3, max_lat=37.9)
    config = load_config("config/providers.yaml")
    fetcher = GISDataFetcher(config=config, cache=Cache(enabled=True))

    results = fetcher.fetch(["weather", "earthquakes"], bbox)

    for result in results:
        if result.ok:
            print(f"{result.provider}: {len(result.features)} feature(s)")
            for feature in result.features[:3]:
                print("   ", feature.properties)
        else:
            print(f"{result.provider}: FAILED - {result.error}")


if __name__ == "__main__":
    main()
