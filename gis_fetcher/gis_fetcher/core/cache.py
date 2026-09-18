"""
A deliberately simple disk cache. Public GIS APIs (Overpass, Open-Elevation,
etc.) are free but rate-limited or occasionally flaky, so caching
identical requests (same provider + same bbox + same params) for a
short TTL saves you from re-hitting them while iterating on a script.

Swap this out for Redis/S3/whatever in production -- the interface
(`get`/`set`/`make_key`) is all the fetcher depends on.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from .base import BBox, Feature


class Cache:
    def __init__(
        self,
        enabled: bool = True,
        directory: str = ".gis_cache",
        ttl_seconds: int = 3600,
    ):
        self.enabled = enabled
        self.dir = Path(directory)
        self.ttl = ttl_seconds  # default/fallback TTL when a caller doesn't override
        if self.enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    def make_key(self, provider: str, bbox: BBox, params: dict) -> str:
        raw = json.dumps(
            {"provider": provider, "bbox": bbox.as_tuple(), "params": params},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str, ttl_seconds: Optional[int] = None) -> Optional[list]:
        """`ttl_seconds`, when given, overrides the cache-wide default for
        this one lookup -- this is how static providers (soil, slope,
        elevation: cache for ~90 days) and dynamic providers (weather,
        river discharge: cache for ~1-6h) share one on-disk cache without
        static data going stale-fast or dynamic data going stale-slow.
        See hazard_map.py for where the per-provider TTL comes from.
        """
        if not self.enabled:
            return None
        path = self.dir / f"{key}.json"
        if not path.exists():
            return None
        ttl = self.ttl if ttl_seconds is None else ttl_seconds
        if time.time() - path.stat().st_mtime > ttl:
            return None  # expired
        with open(path, "r") as f:
            raw = json.load(f)
        return [Feature(**item) for item in raw]

    def set(self, key: str, features: list) -> None:
        if not self.enabled:
            return
        path = self.dir / f"{key}.json"
        with open(path, "w") as f:
            json.dump([f.to_dict() for f in features], f)
