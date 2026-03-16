# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for GCP collector modules (no real GCP credentials required)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from cost_model.models import CostSnapshot

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_bq_row(
    service: str,
    region: str | None,
    usage_date: date,
    total_cost: float,
    currency: str = "USD",
) -> dict[str, Any]:
    return {
        "service": service,
        "region": region,
        "usage_date": usage_date,
        "total_cost": total_cost,
        "currency": currency,
    }


def _dict_row(data: dict[str, Any]) -> Any:
    """Minimal dict-like object that behaves like a BigQuery Row."""

    class _Row:
        def __init__(self, d: dict[str, Any]) -> None:
            self._d = d

        def __getitem__(self, key: str) -> Any:
            return self._d[key]

    return _Row(data)


# ---------------------------------------------------------------------------
# GCPCostCollector
# ---------------------------------------------------------------------------


class TestGCPCostCollector:
    """Tests for GCPCostCollector.collect_costs()."""

    @patch("cloud.gcp.cost_collector.GCPCostCollector.__init__", return_value=None)
    def _make_collector(self, mock_init: MagicMock) -> Any:
        from cloud.gcp.cost_collector import GCPCostCollector

        c = GCPCostCollector.__new__(GCPCostCollector)
        c._project_id = "test-project"
        c._billing_project_id = "billing-project"
        c._billing_dataset = "billing_ds"
        c._billing_table = "gcp_billing_export_v1_ABCDEF"
        return c

    def test_collect_costs_returns_snapshots(self) -> None:
        from cloud.gcp.cost_collector import GCPCostCollector

        collector = GCPCostCollector.__new__(GCPCostCollector)
        collector._project_id = "test-project"
        collector._billing_project_id = "billing-project"
        collector._billing_dataset = "billing_ds"
        collector._billing_table = "gcp_billing_export_v1_ABCDEF"

        rows = [
            _dict_row(_make_bq_row("Compute Engine", "us-central1", date(2025, 3, 1), 120.50)),
            _dict_row(_make_bq_row("Cloud Storage", "us-east1", date(2025, 3, 1), 5.00)),
            _dict_row(_make_bq_row("BigQuery", None, date(2025, 3, 2), 3.75)),
        ]
        mock_bq = MagicMock()
        mock_bq.query.return_value.result.return_value = rows
        collector._bq = mock_bq

        snapshots = collector.collect_costs(date(2025, 3, 1), date(2025, 3, 3))

        assert len(snapshots) == 3
        assert all(isinstance(s, CostSnapshot) for s in snapshots)

        ce = snapshots[0]
        assert ce.provider == "gcp"
        assert ce.account_id == "test-project"
        assert ce.service == "Compute Engine"
        assert ce.region == "us-central1"
        assert ce.cost_usd == pytest.approx(120.50)
        assert ce.period_start == date(2025, 3, 1)

    def test_none_region_defaults_to_global(self) -> None:
        from cloud.gcp.cost_collector import GCPCostCollector

        collector = GCPCostCollector.__new__(GCPCostCollector)
        collector._project_id = "p"
        collector._billing_project_id = "p"
        collector._billing_dataset = "ds"
        collector._billing_table = "t"

        rows = [_dict_row(_make_bq_row("BigQuery", None, date(2025, 3, 1), 10.0))]
        mock_bq = MagicMock()
        mock_bq.query.return_value.result.return_value = rows
        collector._bq = mock_bq

        snapshots = collector.collect_costs(date(2025, 3, 1), date(2025, 3, 2))
        assert snapshots[0].region == "global"

    def test_full_table_property(self) -> None:
        from cloud.gcp.cost_collector import GCPCostCollector

        c = GCPCostCollector.__new__(GCPCostCollector)
        c._billing_project_id = "my-billing-proj"
        c._billing_dataset = "billing_export"
        c._billing_table = "gcp_billing_export_v1_ABCDEF"
        assert c._full_table == "my-billing-proj.billing_export.gcp_billing_export_v1_ABCDEF"


# ---------------------------------------------------------------------------
# GCPResourceCollector — helpers
# ---------------------------------------------------------------------------


class TestGCPResourceCollectorHelpers:
    """Tests for pure helper functions in the resource collector."""

    def test_zone_to_region(self) -> None:
        from cloud.gcp.resource_collector import _zone_to_region

        assert _zone_to_region("us-central1-a") == "us-central1"
        assert _zone_to_region("europe-west1-b") == "europe-west1"
        assert _zone_to_region("asia-east1-c") == "asia-east1"

    def test_daily_cost_running_instance(self) -> None:
        from cloud.gcp.resource_collector import _daily_cost_for_instance

        cost = _daily_cost_for_instance("n1-standard-2", "RUNNING")
        assert cost == pytest.approx(0.095 * 24)

    def test_daily_cost_stopped_instance_is_zero(self) -> None:
        from cloud.gcp.resource_collector import _daily_cost_for_instance

        assert _daily_cost_for_instance("n1-standard-4", "TERMINATED") == 0.0

    def test_daily_cost_unknown_machine_type_is_zero(self) -> None:
        from cloud.gcp.resource_collector import _daily_cost_for_instance

        assert _daily_cost_for_instance("custom-8-16384", "RUNNING") == 0.0

    def test_daily_cost_for_disk(self) -> None:
        from cloud.gcp.resource_collector import _daily_cost_for_disk

        # 100 GB pd-ssd @ $0.17/GB/month
        cost = _daily_cost_for_disk("pd-ssd", 100)
        assert cost == pytest.approx(100 * 0.17 / 30, rel=1e-4)

    def test_daily_cost_for_disk_with_full_path(self) -> None:
        from cloud.gcp.resource_collector import _daily_cost_for_disk

        cost = _daily_cost_for_disk("zones/us-central1-a/diskTypes/pd-standard", 200)
        assert cost == pytest.approx(200 * 0.04 / 30, rel=1e-4)


# ---------------------------------------------------------------------------
# GCPResourceCollector — collect_instances
# ---------------------------------------------------------------------------


def _make_instance(
    name: str,
    machine_type: str = "zones/us-central1-a/machineTypes/e2-standard-2",
    status: str = "RUNNING",
    zone: str = "zones/us-central1-a",
    labels: dict[str, str] | None = None,
    network: str = "default",
) -> MagicMock:
    inst = MagicMock()
    inst.name = name
    inst.machine_type = machine_type
    inst.status = status
    inst.zone = zone
    inst.self_link = f"https://compute.googleapis.com/compute/v1/projects/p/zones/us-central1-a/instances/{name}"
    inst.labels = labels or {}
    iface = MagicMock()
    iface.network = f"projects/p/global/networks/{network}"
    inst.network_interfaces = [iface]
    return inst


class TestGCPResourceCollectorInstances:
    def _make_collector(self) -> Any:
        from cloud.gcp.resource_collector import GCPResourceCollector

        c = GCPResourceCollector.__new__(GCPResourceCollector)
        c._project_id = "test-project"
        c._credentials = None
        c._snapshot_time = datetime(2025, 3, 1, tzinfo=UTC)
        return c

    def test_collect_instances_basic(self) -> None:
        collector = self._make_collector()

        mock_inst = _make_instance("web-server-1")
        mock_client = MagicMock()
        mock_client.aggregated_list.return_value = [
            ("zones/us-central1-a", MagicMock(instances=[mock_inst]))
        ]

        with patch("cloud.gcp.resource_collector.GCPResourceCollector._compute_client", return_value=mock_client):
            with patch("google.cloud.compute_v1.AggregatedListInstancesRequest"):
                snapshots = collector._collect_instances()

        assert len(snapshots) == 1
        s = snapshots[0]
        assert s.provider == "gcp"
        assert s.service == "GCE"
        assert s.type == "compute"
        assert s.name == "web-server-1"
        assert s.state == "running"
        assert s.region == "us-central1"
        assert s.daily_cost > 0

    def test_stopped_instance_zero_cost(self) -> None:
        collector = self._make_collector()

        mock_inst = _make_instance("old-server", status="TERMINATED")
        mock_client = MagicMock()
        mock_client.aggregated_list.return_value = [
            ("zones/us-central1-a", MagicMock(instances=[mock_inst]))
        ]

        with patch("cloud.gcp.resource_collector.GCPResourceCollector._compute_client", return_value=mock_client):
            with patch("google.cloud.compute_v1.AggregatedListInstancesRequest"):
                snapshots = collector._collect_instances()

        assert snapshots[0].daily_cost == 0.0

    def test_empty_zone_skipped(self) -> None:
        collector = self._make_collector()

        mock_client = MagicMock()
        mock_client.aggregated_list.return_value = [
            ("zones/us-central1-a", MagicMock(instances=None))
        ]

        with patch("cloud.gcp.resource_collector.GCPResourceCollector._compute_client", return_value=mock_client):
            with patch("google.cloud.compute_v1.AggregatedListInstancesRequest"):
                snapshots = collector._collect_instances()

        assert snapshots == []


# ---------------------------------------------------------------------------
# GCPResourceCollector — collect_disks
# ---------------------------------------------------------------------------


def _make_disk(
    name: str,
    disk_type: str = "zones/us-central1-a/diskTypes/pd-ssd",
    size_gb: int = 100,
    users: list[str] | None = None,
    zone: str = "zones/us-central1-a",
) -> MagicMock:
    d = MagicMock()
    d.name = name
    d.type_ = disk_type
    d.size_gb = size_gb
    d.users = users or []
    d.zone = zone
    d.self_link = f"https://compute.googleapis.com/compute/v1/projects/p/zones/us-central1-a/disks/{name}"
    d.labels = {}
    return d


class TestGCPResourceCollectorDisks:
    def _make_collector(self) -> Any:
        from cloud.gcp.resource_collector import GCPResourceCollector

        c = GCPResourceCollector.__new__(GCPResourceCollector)
        c._project_id = "test-project"
        c._credentials = None
        c._snapshot_time = datetime(2025, 3, 1, tzinfo=UTC)
        return c

    def test_unattached_disk(self) -> None:
        collector = self._make_collector()
        mock_disk = _make_disk("orphan-disk", users=[])
        mock_client = MagicMock()
        mock_client.aggregated_list.return_value = [
            ("zones/us-central1-a", MagicMock(disks=[mock_disk]))
        ]

        with patch("google.cloud.compute_v1.DisksClient", return_value=mock_client):
            with patch("google.cloud.compute_v1.AggregatedListDisksRequest"):
                snapshots = collector._collect_disks()

        assert len(snapshots) == 1
        assert snapshots[0].state == "unattached"
        assert snapshots[0].service == "PersistentDisk"
        assert snapshots[0].type == "storage"

    def test_attached_disk(self) -> None:
        collector = self._make_collector()
        mock_disk = _make_disk(
            "boot-disk",
            users=["projects/p/zones/us-central1-a/instances/web-1"],
        )
        mock_client = MagicMock()
        mock_client.aggregated_list.return_value = [
            ("zones/us-central1-a", MagicMock(disks=[mock_disk]))
        ]

        with patch("google.cloud.compute_v1.DisksClient", return_value=mock_client):
            with patch("google.cloud.compute_v1.AggregatedListDisksRequest"):
                snapshots = collector._collect_disks()

        assert snapshots[0].state == "attached"
        assert snapshots[0].metadata["attached_to"] == "web-1"


# ---------------------------------------------------------------------------
# GCPCollector — test_connection
# ---------------------------------------------------------------------------


class TestGCPCollector:
    def test_test_connection_success(self) -> None:
        from cloud.gcp.collector import GCPCollector

        c = GCPCollector.__new__(GCPCollector)
        c._project_id = "test-project"

        mock_zone = MagicMock()
        mock_client = MagicMock()
        mock_client.list.return_value = [mock_zone]

        with patch("google.cloud.compute_v1.ZonesClient", return_value=mock_client):
            with patch("google.cloud.compute_v1.ListZonesRequest"):
                assert c.test_connection() is True

    def test_test_connection_failure(self) -> None:
        from cloud.gcp.collector import GCPCollector

        c = GCPCollector.__new__(GCPCollector)
        c._project_id = "bad-project"

        with patch("google.cloud.compute_v1.ZonesClient", side_effect=Exception("permission denied")):
            assert c.test_connection() is False

    def test_collect_costs_no_billing_config_returns_empty(self) -> None:
        from cloud.gcp.collector import GCPCollector

        c = GCPCollector.__new__(GCPCollector)
        c._project_id = "test-project"
        c._cost_collector = None

        result = c.collect_costs(date(2025, 3, 1), date(2025, 3, 31))
        assert result == []


# ---------------------------------------------------------------------------
# Integration placeholder
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_gcp_collector_live() -> None:
    """Requires real GCP credentials and billing export configured."""
    ...
