# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

from datetime import UTC, date, datetime

from cost_model.models import CostSnapshot, ResourceSnapshot
from intelligence.anomaly import (
    detect_cost_spikes,
    detect_new_high_cost_resources,
    detect_sudden_scaling,
)
from intelligence.contributors import top_regions, top_resources, top_services
from intelligence.forecast import compute_forecast
from intelligence.waste import (
    detect_idle_nat_gateways,
    detect_stopped_instances,
    detect_unattached_disks,
    detect_unused_elastic_ips,
    find_all_waste,
)

# -- Anomaly Detection --------------------------------------------------------

class TestCostSpikeDetection:
    def test_detects_spike(self) -> None:
        history = [
            CostSnapshot(
                provider="aws", account_id="a", period_start=date(2025, 3, 1),
                period_end=date(2025, 3, 2), service="EC2", region="us-east-1",
                usage_type="", cost_usd=100.0,
                snapshot_time=datetime(2025, 3, 2, tzinfo=UTC),
            ),
            CostSnapshot(
                provider="aws", account_id="a", period_start=date(2025, 3, 2),
                period_end=date(2025, 3, 3), service="EC2", region="us-east-1",
                usage_type="", cost_usd=200.0,
                snapshot_time=datetime(2025, 3, 3, tzinfo=UTC),
            ),
        ]
        events = detect_cost_spikes(history)
        assert len(events) == 1
        assert events[0].anomaly_type == "cost_spike"
        assert events[0].severity == "high"

    def test_no_spike_within_threshold(self) -> None:
        history = [
            CostSnapshot(
                provider="aws", account_id="a", period_start=date(2025, 3, 1),
                period_end=date(2025, 3, 2), service="EC2", region="us-east-1",
                usage_type="", cost_usd=100.0,
                snapshot_time=datetime(2025, 3, 2, tzinfo=UTC),
            ),
            CostSnapshot(
                provider="aws", account_id="a", period_start=date(2025, 3, 2),
                period_end=date(2025, 3, 3), service="EC2", region="us-east-1",
                usage_type="", cost_usd=110.0,
                snapshot_time=datetime(2025, 3, 3, tzinfo=UTC),
            ),
        ]
        events = detect_cost_spikes(history)
        assert len(events) == 0

    def test_empty_history(self) -> None:
        assert detect_cost_spikes([]) == []


class TestNewHighCostResources:
    def test_detects_high_cost(self) -> None:
        resources = [
            ResourceSnapshot(
                resource_id="i-1", provider="aws", account_id="a",
                type="compute", service="EC2", name="big-box", region="us-east-1",
                daily_cost=100.0, monthly_cost_estimate=3000.0, currency="USD",
                state="running",
                snapshot_time=datetime.now(UTC),
            ),
        ]
        events = detect_new_high_cost_resources(resources)
        assert len(events) == 1
        assert events[0].anomaly_type == "new_high_cost"

    def test_ignores_low_cost(self) -> None:
        resources = [
            ResourceSnapshot(
                resource_id="i-1", provider="aws", account_id="a",
                type="compute", service="EC2", name="small", region="us-east-1",
                daily_cost=5.0, monthly_cost_estimate=150.0, currency="USD",
                state="running",
                snapshot_time=datetime.now(UTC),
            ),
        ]
        events = detect_new_high_cost_resources(resources)
        assert len(events) == 0


class TestSuddenScaling:
    def test_detects_scaling(self) -> None:
        prev = [
            ResourceSnapshot(
                resource_id=f"i-{i}", provider="aws", account_id="a",
                type="compute", service="EC2", name=f"s-{i}", region="us-east-1",
                daily_cost=5.0, monthly_cost_estimate=150.0, currency="USD",
                state="running",
                snapshot_time=datetime.now(UTC),
            )
            for i in range(2)
        ]
        curr = [
            ResourceSnapshot(
                resource_id=f"i-{i}", provider="aws", account_id="a",
                type="compute", service="EC2", name=f"s-{i}", region="us-east-1",
                daily_cost=5.0, monthly_cost_estimate=150.0, currency="USD",
                state="running",
                snapshot_time=datetime.now(UTC),
            )
            for i in range(5)
        ]
        events = detect_sudden_scaling(curr, prev)
        assert len(events) == 1
        assert events[0].anomaly_type == "sudden_scaling"


# -- Waste Detection ----------------------------------------------------------

class TestWasteDetection:
    def test_unattached_disks(self) -> None:
        resources = [
            ResourceSnapshot(
                resource_id="vol-abc", provider="aws", account_id="a",
                type="storage", service="EBS", name="old-vol", region="us-east-1",
                daily_cost=1.0, monthly_cost_estimate=30.0, currency="USD",
                state="unattached",
                metadata={"size_gb": 100, "volume_type": "gp3"},
                snapshot_time=datetime.now(UTC),
            ),
        ]
        findings = detect_unattached_disks(resources)
        assert len(findings) == 1
        assert findings[0].waste_type == "unattached_disk"
        assert findings[0].estimated_monthly_savings == 30.0  # uses monthly_cost_estimate when available

    def test_stopped_instances(self) -> None:
        resources = [
            ResourceSnapshot(
                resource_id="i-stop", provider="aws", account_id="a",
                type="compute", service="EC2", name="stopped-box", region="us-east-1",
                daily_cost=0, monthly_cost_estimate=50.0, currency="USD",
                state="stopped",
                metadata={"instance_type": "m5.large"},
                snapshot_time=datetime.now(UTC),
            ),
        ]
        findings = detect_stopped_instances(resources)
        assert len(findings) == 1
        assert findings[0].waste_type == "stopped_instance"

    def test_idle_nat(self) -> None:
        resources = [
            ResourceSnapshot(
                resource_id="nat-123", provider="aws", account_id="a",
                type="network", service="NAT Gateway", name="", region="us-east-1",
                daily_cost=1.08, monthly_cost_estimate=32.40, currency="USD",
                state="available",
                metadata={"subnet_id": "subnet-abc"},
                snapshot_time=datetime.now(UTC),
            ),
        ]
        findings = detect_idle_nat_gateways(resources)
        assert len(findings) == 1
        assert findings[0].waste_type == "idle_nat"

    def test_unused_eip(self) -> None:
        resources = [
            ResourceSnapshot(
                resource_id="eipalloc-abc", provider="aws", account_id="a",
                type="network", service="ElasticIP", name="", region="us-east-1",
                daily_cost=0.12, monthly_cost_estimate=3.60, currency="USD",
                state="unattached",
                snapshot_time=datetime.now(UTC),
            ),
        ]
        findings = detect_unused_elastic_ips(resources)
        assert len(findings) == 1
        assert findings[0].waste_type == "unused_ip"

    def test_find_all_waste(self) -> None:
        resources = [
            ResourceSnapshot(
                resource_id="vol-1", provider="aws", account_id="a",
                type="storage", service="EBS", name="", region="us-east-1",
                daily_cost=0, monthly_cost_estimate=0, currency="USD",
                state="unattached", metadata={"size_gb": 50, "volume_type": "gp3"},
                snapshot_time=datetime.now(UTC),
            ),
            ResourceSnapshot(
                resource_id="i-stop", provider="aws", account_id="a",
                type="compute", service="EC2", name="", region="us-east-1",
                daily_cost=0, monthly_cost_estimate=25.0, currency="USD",
                state="stopped", metadata={"instance_type": "t3.small"},
                snapshot_time=datetime.now(UTC),
            ),
        ]
        findings = find_all_waste(resources)
        types = {f.waste_type for f in findings}
        assert "unattached_disk" in types
        assert "stopped_instance" in types

    def test_no_waste_for_healthy_resources(self) -> None:
        resources = [
            ResourceSnapshot(
                resource_id="i-ok", provider="aws", account_id="a",
                type="compute", service="EC2", name="healthy", region="us-east-1",
                daily_cost=10, monthly_cost_estimate=300, currency="USD",
                state="running",
                snapshot_time=datetime.now(UTC),
            ),
        ]
        findings = find_all_waste(resources)
        assert len(findings) == 0


# -- Forecast -----------------------------------------------------------------

class TestForecast:
    def test_basic_forecast(self) -> None:
        history = [
            CostSnapshot(
                provider="aws", account_id="a",
                period_start=date(2025, 3, i + 1),
                period_end=date(2025, 3, i + 2),
                service="EC2", region="us-east-1", usage_type="",
                cost_usd=100.0,
                snapshot_time=datetime(2025, 3, i + 1, tzinfo=UTC),
            )
            for i in range(14)
        ]
        results = compute_forecast(history, target_month=date(2025, 3, 1))
        assert len(results) == 1
        assert results[0].avg_daily_cost == 100.0
        assert results[0].projected_monthly_cost == 3100.0  # 100 * 31 days in March
        assert results[0].trend_direction == "stable"

    def test_empty_history(self) -> None:
        assert compute_forecast([]) == []

    def test_increasing_trend(self) -> None:
        history = [
            CostSnapshot(
                provider="aws", account_id="a",
                period_start=date(2025, 3, i + 1),
                period_end=date(2025, 3, i + 2),
                service="EC2", region="us-east-1", usage_type="",
                cost_usd=50.0 + i * 10.0,
                snapshot_time=datetime(2025, 3, i + 1, tzinfo=UTC),
            )
            for i in range(14)
        ]
        results = compute_forecast(history, target_month=date(2025, 3, 1))
        assert len(results) == 1
        assert results[0].trend_direction == "increasing"


# -- Contributors -------------------------------------------------------------

class TestContributors:
    def test_top_services(self) -> None:
        history = [
            CostSnapshot(
                provider="aws", account_id="a", period_start=date(2025, 3, 1),
                period_end=date(2025, 3, 2), service="EC2", region="us-east-1",
                usage_type="", cost_usd=100.0,
                snapshot_time=datetime.now(UTC),
            ),
            CostSnapshot(
                provider="aws", account_id="a", period_start=date(2025, 3, 1),
                period_end=date(2025, 3, 2), service="RDS", region="us-east-1",
                usage_type="", cost_usd=50.0,
                snapshot_time=datetime.now(UTC),
            ),
        ]
        result = top_services(history)
        assert len(result) == 2
        assert result[0].name == "EC2"
        assert result[0].percentage > result[1].percentage

    def test_top_regions(self) -> None:
        history = [
            CostSnapshot(
                provider="aws", account_id="a", period_start=date(2025, 3, 1),
                period_end=date(2025, 3, 2), service="EC2", region="us-east-1",
                usage_type="", cost_usd=200.0,
                snapshot_time=datetime.now(UTC),
            ),
            CostSnapshot(
                provider="aws", account_id="a", period_start=date(2025, 3, 1),
                period_end=date(2025, 3, 2), service="EC2", region="eu-west-1",
                usage_type="", cost_usd=100.0,
                snapshot_time=datetime.now(UTC),
            ),
        ]
        result = top_regions(history)
        assert result[0].name == "us-east-1"

    def test_top_resources(self) -> None:
        resources = [
            ResourceSnapshot(
                resource_id=f"i-{i}", provider="aws", account_id="a",
                type="compute", service="EC2", name=f"srv-{i}", region="us-east-1",
                daily_cost=float((5 - i) * 10), monthly_cost_estimate=0,
                currency="USD", state="running",
                snapshot_time=datetime.now(UTC),
            )
            for i in range(5)
        ]
        result = top_resources(resources)
        assert result[0].total_cost_usd == 50.0

    def test_empty_data(self) -> None:
        assert top_services([]) == []
        assert top_regions([]) == []
        assert top_resources([]) == []
