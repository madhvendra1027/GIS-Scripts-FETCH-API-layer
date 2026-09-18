"""
Core data model + the interface every provider must implement.

Everything else in the framework (registry, fetcher, cache, cli) only
ever talks to providers through this interface, which is what makes it
possible to add new APIs without touching existing code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Optional
import datetime as dt


@dataclass
class BBox:
    """A simple lon/lat bounding box, WGS84 (the coordinate system almost
    every public geospatial API expects)."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def as_tuple(self) -> tuple:
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)

    @property
    def center(self) -> tuple:
        return (
            (self.min_lon + self.max_lon) / 2,
            (self.min_lat + self.max_lat) / 2,
        )

    def __post_init__(self):
        if self.min_lon > self.max_lon or self.min_lat > self.max_lat:
            raise ValueError("BBox min values must be <= max values")


@dataclass
class Feature:
    """One normalized geospatial feature. Every provider, no matter what
    shape the upstream API returns, must translate its results into a
    list of these before handing them back to the fetcher."""

    geometry: dict  # a GeoJSON geometry dict, e.g. {"type": "Point", "coordinates": [...]}
    properties: dict
    source: str
    fetched_at: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())

    def to_geojson_feature(self) -> dict:
        return {
            "type": "Feature",
            "geometry": self.geometry,
            "properties": {
                **self.properties,
                "_source": self.source,
                "_fetched_at": self.fetched_at,
            },
        }

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FetchResult:
    """Outcome of asking one provider for data. Kept separate from
    Feature so that a failed provider doesn't take the whole batch down
    -- callers can inspect `.ok` and `.error` per-provider."""

    provider: str
    ok: bool
    features: list
    error: Optional[str] = None

    def to_geojson(self) -> dict:
        return {
            "type": "FeatureCollection",
            "features": [f.to_geojson_feature() for f in self.features],
        }


class GISDataProvider(ABC):
    """Base class for every data source adapter.

    Subclasses must set `name` and implement `fetch()`. Everything about
    HTTP (the aiohttp session, retries, rate limiting, caching) is
    handled by the fetcher -- providers only need to know how to build a
    request for their API and how to translate the response into
    `Feature` objects.
    """

    name: str = "base"
    requires_api_key: bool = False

    def __init__(self, session, config: Optional[dict] = None):
        self.session = session
        self.config = config or {}

    @abstractmethod
    async def fetch(self, bbox: BBox, **params: Any) -> list:
        """Query the upstream API for `bbox` and return a list of
        `Feature` objects. Raise on failure -- the fetcher handles
        retries, it does not expect providers to swallow errors."""
        raise NotImplementedError
