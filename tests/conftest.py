# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import tempfile
from collections.abc import Generator
from datetime import UTC, date, datetime

import pytest

from cost_model.models import AnomalyEvent, CostSnapshot, ResourceSnapshot
from storage.sqlite_adapter import SQLiteAdapter


@pytest.fixture
def tmp_db() -> Generator[SQLiteAdapter, None, None]:
    """Provide a fresh in-memory-like SQLite adapter using a temp file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        adapter = SQLiteAdapter(f.name)
    yield adapter
    adapter.close()


@pytest.fixture
def sample_resource() -> ResourceSnapshot:
    return ResourceSnapshot(
        resource_id="i-abc123",
        provider="aws",
        account_id="123456789012",
        type="compute",
        service="EC2",
        name="web-server-1",
        region="us-east-1",
        daily_cost=10.50,
        monthly_cost_estimate=315.00,
        currency="USD",
        state="running",
        tags={"env": "production"},
        metadata={"instance_type": "t3.medium"},
        snapshot_time=datetime(2025, 3, 15, 12, 0, tzinfo=UTC),
    )


@pytest.fixture
def sample_cost_snapshot() -> CostSnapshot:
    return CostSnapshot(
        provider="aws",
        account_id="123456789012",
        period_start=date(2025, 3, 1),
        period_end=date(2025, 3, 2),
        service="Amazon Elastic Compute Cloud - Compute",
        region="us-east-1",
        usage_type="BoxUsage:t3.medium",
        cost_usd=42.50,
        snapshot_time=datetime(2025, 3, 15, 12, 0, tzinfo=UTC),
    )


@pytest.fixture
def sample_anomaly() -> AnomalyEvent:
    return AnomalyEvent(
        provider="aws",
        account_id="123456789012",
        resource_id="EC2/us-east-1",
        anomaly_type="cost_spike",
        severity="high",
        detail={"previous_cost": 10.0, "current_cost": 50.0},
        detected_at=datetime(2025, 3, 15, 12, 0, tzinfo=UTC),
    )
