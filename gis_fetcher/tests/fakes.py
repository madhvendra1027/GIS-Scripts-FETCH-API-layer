"""
A minimal stand-in for `aiohttp.ClientSession` that implements just
enough of the interface (`get`/`post` returning an async context
manager with `.raise_for_status()` and `.json()`) for provider unit
tests. Avoids depending on a real network call or on mocking libraries
that are tightly (and sometimes fragile-ly) coupled to aiohttp's
internal version.
"""

from __future__ import annotations


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def json(self):
        return self._payload


class FakeSession:
    """Records the last request made and always returns a canned payload."""

    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self._status = status
        self.last_call = None

    def get(self, url, params=None, **kwargs):
        self.last_call = ("GET", url, params)
        return FakeResponse(self._payload, self._status)

    def post(self, url, data=None, json=None, **kwargs):
        self.last_call = ("POST", url, data if data is not None else json)
        return FakeResponse(self._payload, self._status)
