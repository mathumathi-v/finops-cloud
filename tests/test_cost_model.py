# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

from datetime import date, datetime

from cost_model.models import AnomalyEvent, CostSnapshot, ResourceSnapshot


class TestResourceSnapshot:
    def test_create(self) -> None:
        r = ResourceSnapshot(
            resource_id="i-123",
            provider="aws",
            account_id="acct",
            type="compute",
            service="EC2",
            name="test",
            region="us-east-1",
            daily_cost=5.0,
            monthly_cost_estimate=150.0,
            currency="USD",
            state="running",
        )
        assert r.resource_id == "i-123"
        assert r.provider == "aws"
        assert r.daily_cost == 5.0
        assert r.tags == {}
        assert r.metadata == {}

    def test_default_tags_not_shared(self) -> None:
        a = ResourceSnapshot(
            resource_id="a", provider="aws", account_id="x",
            type="compute", service="EC2", name="a", region="us-east-1",
            daily_cost=0, monthly_cost_estimate=0, currency="USD", state="running",
        )
        b = ResourceSnapshot(
            resource_id="b", provider="aws", account_id="x",
            type="compute", service="EC2", name="b", region="us-east-1",
            daily_cost=0, monthly_cost_estimate=0, currency="USD", state="running",
        )
        a.tags["key"] = "val"
        assert "key" not in b.tags


class TestCostSnapshot:
    def test_create(self) -> None:
        cs = CostSnapshot(
            provider="aws",
            account_id="acct",
            period_start=date(2025, 3, 1),
            period_end=date(2025, 3, 2),
            service="EC2",
            region="us-east-1",
            usage_type="BoxUsage",
            cost_usd=10.0,
        )
        assert cs.cost_usd == 10.0
        assert cs.period_start == date(2025, 3, 1)


class TestAnomalyEvent:
    def test_create(self) -> None:
        ae = AnomalyEvent(
            provider="aws",
            account_id="acct",
            resource_id="i-123",
            anomaly_type="cost_spike",
            severity="high",
        )
        assert ae.anomaly_type == "cost_spike"
        assert ae.detail == {}
        assert isinstance(ae.detected_at, datetime)
