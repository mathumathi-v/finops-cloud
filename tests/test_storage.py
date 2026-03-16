# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

from datetime import UTC, date, datetime

from cost_model.models import AnomalyEvent, CostSnapshot, ResourceSnapshot
from storage.sqlite_adapter import SQLiteAdapter


class TestSQLiteAdapter:
    def test_save_and_get_resource_snapshots(self, tmp_db: SQLiteAdapter) -> None:
        resource = ResourceSnapshot(
            resource_id="i-abc",
            provider="aws",
            account_id="123",
            type="compute",
            service="EC2",
            name="test",
            region="us-east-1",
            daily_cost=10.0,
            monthly_cost_estimate=300.0,
            currency="USD",
            state="running",
            tags={"env": "prod"},
            metadata={"instance_type": "t3.medium"},
            snapshot_time=datetime(2025, 3, 15, 12, 0, tzinfo=UTC),
        )
        tmp_db.save_resource_snapshots([resource])

        results = tmp_db.get_resource_snapshots("aws")
        assert len(results) == 1
        assert results[0].resource_id == "i-abc"
        assert results[0].tags == {"env": "prod"}
        assert results[0].metadata["instance_type"] == "t3.medium"

    def test_save_and_get_cost_snapshots(self, tmp_db: SQLiteAdapter) -> None:
        cost = CostSnapshot(
            provider="aws",
            account_id="123",
            period_start=date.today(),
            period_end=date.today(),
            service="EC2",
            region="us-east-1",
            usage_type="BoxUsage",
            cost_usd=42.0,
            snapshot_time=datetime.now(UTC),
        )
        tmp_db.save_cost_snapshots([cost])

        results = tmp_db.get_cost_history("aws", days=7)
        assert len(results) == 1
        assert results[0].cost_usd == 42.0
        assert results[0].service == "EC2"

    def test_save_and_get_anomaly_events(self, tmp_db: SQLiteAdapter) -> None:
        event = AnomalyEvent(
            provider="aws",
            account_id="123",
            resource_id="i-abc",
            anomaly_type="cost_spike",
            severity="high",
            detail={"increase_pct": 150},
            detected_at=datetime.now(UTC),
        )
        tmp_db.save_anomaly_events([event])

        results = tmp_db.get_anomaly_events("aws", days=7)
        assert len(results) == 1
        assert results[0].anomaly_type == "cost_spike"
        assert results[0].detail["increase_pct"] == 150

    def test_empty_results(self, tmp_db: SQLiteAdapter) -> None:
        assert tmp_db.get_resource_snapshots("aws") == []
        assert tmp_db.get_cost_history("aws", days=30) == []
        assert tmp_db.get_anomaly_events("aws", days=30) == []

    def test_multiple_snapshots(self, tmp_db: SQLiteAdapter) -> None:
        resources = [
            ResourceSnapshot(
                resource_id=f"i-{i}",
                provider="aws",
                account_id="123",
                type="compute",
                service="EC2",
                name=f"server-{i}",
                region="us-east-1",
                daily_cost=float(i * 10),
                monthly_cost_estimate=float(i * 300),
                currency="USD",
                state="running",
                snapshot_time=datetime.now(UTC),
            )
            for i in range(5)
        ]
        tmp_db.save_resource_snapshots(resources)

        results = tmp_db.get_resource_snapshots("aws")
        assert len(results) == 5
