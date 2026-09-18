"""hazard_reading_store.py — the persistence boundary between data_pipeline
and ml_service. ml_service never talks to gis_fetcher, normalize.py, or
cleaning.py directly; it only ever reads HazardReading rows written here.

Backed by SQLite for simplicity — swap the connection for Postgres/PostGIS
later without changing any of the public methods below.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Optional

from data_pipeline.models import DataQuality, HazardReading, HazardType

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hazard_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_id TEXT NOT NULL,
    hazard_type TEXT NOT NULL,
    source TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    parameters TEXT NOT NULL,
    data_quality TEXT NOT NULL,
    UNIQUE(zone_id, hazard_type, source, recorded_at)
);
CREATE INDEX IF NOT EXISTS idx_zone_hazard
    ON hazard_readings(zone_id, hazard_type);
"""


class HazardReadingStore:
    def __init__(self, db_path: str = "hazard_readings.db") -> None:
        self.db_path = db_path
        parent = Path(db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def save(self, reading: HazardReading) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO hazard_readings
                   (zone_id, hazard_type, source, recorded_at, parameters, data_quality)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    reading.zone_id,
                    reading.hazard_type.value,
                    reading.source,
                    reading.recorded_at.isoformat(),
                    json.dumps(reading.parameters),
                    reading.data_quality.value,
                ),
            )
            conn.commit()

    def latest_for_zone(self, zone_id: str, hazard_type: HazardType) -> Optional[HazardReading]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                """SELECT zone_id, hazard_type, source, recorded_at, parameters, data_quality
                   FROM hazard_readings
                   WHERE zone_id = ? AND hazard_type = ?
                   ORDER BY recorded_at DESC LIMIT 1""",
                (zone_id, hazard_type.value),
            ).fetchone()
        return self._row_to_reading(row) if row else None

    def history_for_zone(
        self, zone_id: str, hazard_type: HazardType, limit: int = 100
    ) -> list[HazardReading]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            rows = conn.execute(
                """SELECT zone_id, hazard_type, source, recorded_at, parameters, data_quality
                   FROM hazard_readings
                   WHERE zone_id = ? AND hazard_type = ?
                   ORDER BY recorded_at DESC LIMIT ?""",
                (zone_id, hazard_type.value, limit),
            ).fetchall()
        return [self._row_to_reading(r) for r in rows]

    @staticmethod
    def _row_to_reading(row: tuple) -> HazardReading:
        zone_id, hazard_type, source, recorded_at, parameters, data_quality = row
        return HazardReading(
            zone_id=zone_id,
            hazard_type=HazardType(hazard_type),
            source=source,
            recorded_at=datetime.fromisoformat(recorded_at),
            parameters=json.loads(parameters),
            data_quality=DataQuality(data_quality),
        )
