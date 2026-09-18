"""store.py — persistence for fields that come from a one-time static
dataset ingestion instead of a live API (see ../../STATIC_DATASETS.md
for which fields these are and why: shoreline_change_rate_m_per_yr,
sediment_type_code, mangrove_cover_pct, and per-zone historical_*_count).

Deliberately a separate table from `hazard_readings` (HazardReadingStore):
those rows are hazard-scoped, timestamped observations that get a new row
every pipeline run; these are zone-scoped facts that get overwritten in
place when you re-ingest a newer dataset vintage (e.g. next year's
Global Mangrove Watch release). Mixing the two would mean either
re-inserting a "new" static row on every live pipeline run for no reason,
or teaching HazardReadingStore two different update semantics.

`pipeline_runner.py` reads from this store and merges its values into
`raw_parameters` before `cleaning.py` runs -- once a field is ingested
here, every future live run picks it up automatically with zero further
manual work. That's the actual "automation" this buys you: the *dataset*
is still a one-time human download (see STATIC_DATASETS.md for why no
live API exists), but wiring it into every subsequent run is not.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS static_zone_fields (
    zone_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    value REAL,
    source TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (zone_id, field_name)
);
"""


@dataclass
class StaticField:
    zone_id: str
    field_name: str
    value: Optional[float]
    source: str
    ingested_at: datetime


class StaticDatasetStore:
    def __init__(self, db_path: str = "static_zone_data.db") -> None:
        self.db_path = db_path
        parent = Path(db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def upsert(self, zone_id: str, field_name: str, value: Optional[float], source: str) -> None:
        """Insert or overwrite one zone/field's static value. Re-running
        a loader script with a newer dataset vintage (e.g. next year's
        mangrove extent) just calls this again -- last ingested wins,
        which is correct for a slow-changing fact, not an observation
        series."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """INSERT INTO static_zone_fields (zone_id, field_name, value, source, ingested_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(zone_id, field_name) DO UPDATE SET
                       value=excluded.value, source=excluded.source, ingested_at=excluded.ingested_at""",
                (zone_id, field_name, value, source, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    def get(self, zone_id: str, field_name: str) -> Optional[StaticField]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                """SELECT zone_id, field_name, value, source, ingested_at
                   FROM static_zone_fields WHERE zone_id = ? AND field_name = ?""",
                (zone_id, field_name),
            ).fetchone()
        if row is None:
            return None
        zone_id, field_name, value, source, ingested_at = row
        return StaticField(zone_id, field_name, value, source, datetime.fromisoformat(ingested_at))

    def get_all_for_zone(self, zone_id: str) -> dict[str, float]:
        """What pipeline_runner.py actually calls: every static field
        ingested for this zone, as a plain {field_name: value} dict ready
        to merge into raw_parameters. Missing fields are simply absent
        (not None) so merge_shared_fields()'s "later dict wins only on
        non-None" rule can't accidentally overwrite a live value with a
        stale static None.
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT field_name, value FROM static_zone_fields WHERE zone_id = ?",
                (zone_id,),
            ).fetchall()
        return {field_name: value for field_name, value in rows if value is not None}

    def needs_refresh(self, zone_id: str, field_name: str, max_age_days: float) -> bool:
        """True if `field_name` has never been ingested for `zone_id`,
        or was ingested more than `max_age_days` ago. This is the one
        piece of logic auto_refresh.py needs and didn't have anywhere
        else to live without duplicating `get()`'s date parsing in a
        second place -- a stale-but-present static row (a demo run last
        month, an old proxy fetch) should still trigger a re-fetch, not
        just an absent row."""
        field = self.get(zone_id, field_name)
        if field is None or field.value is None:
            return True
        age = datetime.now(timezone.utc) - field.ingested_at
        return age.total_seconds() > max_age_days * 86400

    def coverage_report(self) -> dict[str, list[str]]:
        """field_name -> list of zone_ids that have it ingested. Useful
        for a quick 'what have I actually loaded so far' sanity check."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute("SELECT field_name, zone_id FROM static_zone_fields").fetchall()
        report: dict[str, list[str]] = {}
        for field_name, zone_id in rows:
            report.setdefault(field_name, []).append(zone_id)
        return report
