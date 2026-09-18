"""
The orchestrator. This is the only piece of code that knows how to
talk to *many* providers at once: it fans a request out to every
requested provider concurrently (via asyncio + a shared aiohttp
session), applies per-provider rate limiting and retry-with-backoff,
and checks/populates the cache. Adding a new provider never requires
touching this file.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiohttp

from .base import BBox, FetchResult
from .cache import Cache
from .registry import get_provider

logger = logging.getLogger("gis_fetcher")


class GISDataFetcher:
    def __init__(
        self,
        config: Optional[dict] = None,
        cache: Optional[Cache] = None,
        timeout_seconds: int = 30,
    ):
        self.config = config or {"providers": {}}
        self.cache = cache or Cache(enabled=False)
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

        # per-provider state for rate limiting
        self._last_call_at: dict = {}
        self._locks: dict = {}

    def _provider_config(self, name: str) -> dict:
        return self.config.get("providers", {}).get(name, {})

    async def _respect_rate_limit(self, name: str) -> None:
        min_interval = self._provider_config(name).get("min_interval_seconds", 0)
        if min_interval <= 0:
            return
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            elapsed = time.monotonic() - self._last_call_at.get(name, 0)
            wait = min_interval - elapsed
            if wait > 0:
                logger.debug("rate limiting %s: sleeping %.2fs", name, wait)
                await asyncio.sleep(wait)
            self._last_call_at[name] = time.monotonic()

    async def _fetch_one(
        self, session: aiohttp.ClientSession, name: str, bbox: BBox, params: dict
    ) -> FetchResult:
        provider_config = self._provider_config(name)

        cache_key = self.cache.make_key(name, bbox, params)
        # cache_ttl_seconds is read per-provider from providers.yaml (static
        # providers like soil/slope/elevation set this high; dynamic ones
        # like weather/river_discharge set it low). Falls back to the
        # Cache instance's own default when a provider doesn't set one.
        ttl_override = provider_config.get("cache_ttl_seconds")
        cached = self.cache.get(cache_key, ttl_seconds=ttl_override)
        if cached is not None:
            logger.info("cache hit for provider '%s'", name)
            return FetchResult(provider=name, ok=True, features=cached)

        try:
            provider_cls = get_provider(name)
        except KeyError as exc:
            return FetchResult(provider=name, ok=False, features=[], error=str(exc))

        provider = provider_cls(session=session, config=provider_config)
        max_retries = provider_config.get("max_retries", 3)

        last_error = None
        for attempt in range(1, max_retries + 1):
            await self._respect_rate_limit(name)
            try:
                features = await provider.fetch(bbox, **params)
                self.cache.set(cache_key, features)
                return FetchResult(provider=name, ok=True, features=features)
            except Exception as exc:  # noqa: BLE001 - deliberately broad, we log + retry
                last_error = str(exc)
                backoff = min(2 ** attempt, 15)
                logger.warning(
                    "provider '%s' attempt %d/%d failed: %s (retrying in %ds)",
                    name,
                    attempt,
                    max_retries,
                    last_error,
                    backoff,
                )
                if attempt < max_retries:
                    await asyncio.sleep(backoff)

        return FetchResult(provider=name, ok=False, features=[], error=last_error)

    async def fetch_many(self, providers: list, bbox: BBox, params: Optional[dict] = None) -> list:
        params = params or {}
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            tasks = [self._fetch_one(session, name, bbox, params) for name in providers]
            return await asyncio.gather(*tasks)

    def fetch(self, providers: list, bbox: BBox, params: Optional[dict] = None) -> list:
        """Synchronous convenience wrapper around `fetch_many` for
        scripts/CLIs that aren't already inside an event loop."""
        return asyncio.run(self.fetch_many(providers, bbox, params))
