"""
gis_fetcher
===========

A small, extensible framework for pulling *live* geospatial data from
multiple public APIs and normalizing it into a common GeoJSON-like shape.

The only thing you need to do to add a new data source is drop a new
file in `gis_fetcher/providers/`, subclass `GISDataProvider`, and
register it with `@register_provider("name")`. Nothing else in the
codebase needs to change.
"""

__version__ = "0.1.0"

# Importing gis_fetcher (any submodule -- Python always runs this
# __init__.py first) always registers every provider, by importing the
# package whose own __init__.py imports every provider module and runs
# their @register_provider(...) decorators as a side effect. cli.py used
# to do this import itself, which is why `gis-fetcher` (CLI) worked while
# `from gis_fetcher.hazard_map import ...` (hazard_platform's
# pipeline_runner.py / backend/api.py) silently got an empty registry --
# hazard_map.py never imported .providers on its own path. Doing it here
# instead makes every entry point register providers the same way, rather
# than requiring each new entry point to remember this import too.
from . import providers  # noqa: F401
