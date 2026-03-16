# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cost_model.models import AnomalyEvent, CostSnapshot, ResourceSnapshot
from storage.base import StorageAdapter

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS resource_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    account_id TEXT NOT NULL,
    type TEXT,
    service TEXT,
    name TEXT,
    region TEXT,
    daily_cost REAL,
    monthly_cost_estimate REAL,
    state TEXT,
    tags TEXT,
    metadata TEXT,
    snapshot_time TEXT
);

CREATE TABLE IF NOT EXISTS cost_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT,
    account_id TEXT,
    period_start TEXT,
    period_end TEXT,
    service TEXT,
    region TEXT,
    usage_type TEXT,
    cost_usd REAL,
    snapshot_time TEXT
);

CREATE TABLE IF NOT EXISTS anomaly_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT,
    account_id TEXT,
    resource_id TEXT,
    anomaly_type TEXT,
    severity TEXT,
    detail TEXT,
    detected_at TEXT
);
"""


class SQLiteAdapter(StorageAdapter):
    """SQLite-backed storage for cost, resource, and anomaly data."""

    def __init__(self, db_path: str) -> None:
        path = Path(db_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # -- writes ----------------------------------------------------------------

    def save_resource_snapshots(self, snapshots: list[ResourceSnapshot]) -> None:
        """Persist a batch of resource snapshots."""
        rows = [
            (
                s.resource_id,
                s.provider,
                s.account_id,
                s.type,
                s.service,
                s.name,
                s.region,
                s.daily_cost,
                s.monthly_cost_estimate,
                s.state,
                json.dumps(s.tags),
                json.dumps(s.metadata, default=str),
                s.snapshot_time.isoformat(),
            )
            for s in snapshots
        ]
        self._conn.executemany(
            "INSERT INTO resource_snapshots "
            "(resource_id, provider, account_id, type, service, name, region, "
            "daily_cost, monthly_cost_estimate, state, tags, metadata, snapshot_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        logger.info("Saved %d resource snapshots", len(snapshots))

    def save_cost_snapshots(self, snapshots: list[CostSnapshot]) -> None:
        """Persist a batch of cost snapshots."""
        rows = [
            (
                s.provider,
                s.account_id,
                s.period_start.isoformat(),
                s.period_end.isoformat(),
                s.service,
                s.region,
                s.usage_type,
                s.cost_usd,
                s.snapshot_time.isoformat(),
            )
            for s in snapshots
        ]
        self._conn.executemany(
            "INSERT INTO cost_snapshots "
            "(provider, account_id, period_start, period_end, service, region, "
            "usage_type, cost_usd, snapshot_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        logger.info("Saved %d cost snapshots", len(snapshots))

    def save_anomaly_events(self, events: list[AnomalyEvent]) -> None:
        """Persist detected anomaly events."""
        rows = [
            (
                e.provider,
                e.account_id,
                e.resource_id,
                e.anomaly_type,
                e.severity,
                json.dumps(e.detail, default=str),
                e.detected_at.isoformat(),
            )
            for e in events
        ]
        self._conn.executemany(
            "INSERT INTO anomaly_events "
            "(provider, account_id, resource_id, anomaly_type, severity, detail, detected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        logger.info("Saved %d anomaly events", len(events))

    # -- reads -----------------------------------------------------------------

    def get_cost_history(self, provider: str, days: int) -> list[CostSnapshot]:
        """Return cost snapshots for the last N days."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
        cursor = self._conn.execute(
            "SELECT * FROM cost_snapshots WHERE provider = ? AND period_start >= ? "
            "ORDER BY period_start DESC",
            (provider, cutoff),
        )
        return [self._row_to_cost_snapshot(row) for row in cursor.fetchall()]

    def get_resource_snapshots(self, provider: str) -> list[ResourceSnapshot]:
        """Return the most recent resource snapshots for a provider."""
        cursor = self._conn.execute(
            "SELECT * FROM resource_snapshots WHERE provider = ? "
            "ORDER BY snapshot_time DESC",
            (provider,),
        )
        return [self._row_to_resource_snapshot(row) for row in cursor.fetchall()]

    def get_anomaly_events(self, provider: str, days: int) -> list[AnomalyEvent]:
        """Return anomaly events for the last N days."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        cursor = self._conn.execute(
            "SELECT * FROM anomaly_events WHERE provider = ? AND detected_at >= ? "
            "ORDER BY detected_at DESC",
            (provider, cutoff),
        )
        return [self._row_to_anomaly_event(row) for row in cursor.fetchall()]

    # -- row mappers -----------------------------------------------------------

    @staticmethod
    def _row_to_resource_snapshot(row: sqlite3.Row) -> ResourceSnapshot:
        return ResourceSnapshot(
            resource_id=row["resource_id"],
            provider=row["provider"],
            account_id=row["account_id"],
            type=row["type"],
            service=row["service"],
            name=row["name"],
            region=row["region"],
            daily_cost=row["daily_cost"],
            monthly_cost_estimate=row["monthly_cost_estimate"],
            currency="USD",
            state=row["state"],
            tags=json.loads(row["tags"]) if row["tags"] else {},
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            snapshot_time=datetime.fromisoformat(row["snapshot_time"]),
        )

    @staticmethod
    def _row_to_cost_snapshot(row: sqlite3.Row) -> CostSnapshot:
        return CostSnapshot(
            provider=row["provider"],
            account_id=row["account_id"],
            period_start=date.fromisoformat(row["period_start"]),
            period_end=date.fromisoformat(row["period_end"]),
            service=row["service"],
            region=row["region"],
            usage_type=row["usage_type"],
            cost_usd=row["cost_usd"],
            snapshot_time=datetime.fromisoformat(row["snapshot_time"]),
        )

    @staticmethod
    def _row_to_anomaly_event(row: sqlite3.Row) -> AnomalyEvent:
        return AnomalyEvent(
            provider=row["provider"],
            account_id=row["account_id"],
            resource_id=row["resource_id"],
            anomaly_type=row["anomaly_type"],
            severity=row["severity"],
            detail=json.loads(row["detail"]) if row["detail"] else {},
            detected_at=datetime.fromisoformat(row["detected_at"]),
        )
