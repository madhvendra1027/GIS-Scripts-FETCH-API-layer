"""
A tiny registry that maps a short string name (e.g. "weather") to a
provider class. This is the whole mechanism that makes the framework
"scalable to multiple APIs": adding a new source never means editing
the fetcher, the CLI, or any other existing file -- it only means
writing a new provider module and decorating its class.
"""

from __future__ import annotations
from typing import Dict, Type

_REGISTRY: Dict[str, Type] = {}


def register_provider(name: str):
    """Class decorator. Usage:

        @register_provider("weather")
        class OpenMeteoProvider(GISDataProvider):
            ...
    """

    def wrapper(cls):
        if name in _REGISTRY and _REGISTRY[name] is not cls:
            raise ValueError(f"Provider name '{name}' is already registered to {_REGISTRY[name]!r}")
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return wrapper


def get_provider(name: str) -> Type:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"No provider registered under '{name}'. Available providers: {available_providers()}"
        ) from exc


def available_providers() -> list:
    return sorted(_REGISTRY.keys())


def _reset_registry_for_tests() -> None:
    """Test-only helper. Not used by the application itself."""
    _REGISTRY.clear()
