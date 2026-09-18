"""One-time static-dataset ingestion for fields with no live API.

See STATIC_DATASETS.md at the repo root for the full source list and
rationale. `store.StaticDatasetStore` is the persistence layer;
`pipeline_runner.py` reads from it automatically on every live run once
you've ingested a field once via one of the load_*.py scripts here.
"""

from .store import StaticDatasetStore  # noqa: F401
