# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for OCI collector modules (no real OCI credentials required)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_collector() -> Any:
    """Return an OCIResourceCollector with mocked config."""
    from cloud.oci.resource_collector import OCIResourceCollector

    c = OCIResourceCollector.__new__(OCIResourceCollector)
    c._compartment_id = "ocid1.compartment.oc1..test"
    c._config = {"tenancy": "ocid1.tenancy.oc1..test", "region": "us-ashburn-1"}
    c._snapshot_time = datetime(2025, 6, 1, tzinfo=UTC)
    return c


# ---------------------------------------------------------------------------
# Helpers — pure functions
# ---------------------------------------------------------------------------


class TestOCIResourceHelpers:
    def test_daily_cost_running_flex_instance(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_instance

        cost = _daily_cost_for_instance("VM.Standard.E4.Flex", 4.0, "running")
        assert cost == pytest.approx(0.025 * 4.0 * 24, rel=1e-4)

    def test_daily_cost_stopped_instance_zero(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_instance

        assert _daily_cost_for_instance("VM.Standard.E4.Flex", 4.0, "stopped") == 0.0

    def test_daily_cost_terminated_instance_zero(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_instance

        assert _daily_cost_for_instance("VM.Standard.E4.Flex", 4.0, "terminated") == 0.0

    def test_daily_cost_fixed_shape(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_instance

        cost = _daily_cost_for_instance("VM.Standard2.1", 1.0, "running")
        assert cost == pytest.approx(0.0638 * 24, rel=1e-4)

    def test_daily_cost_arm_flex(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_instance

        cost = _daily_cost_for_instance("VM.Standard.A1.Flex", 2.0, "running")
        assert cost == pytest.approx(0.01 * 2.0 * 24, rel=1e-4)

    def test_daily_cost_unknown_shape(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_instance

        assert _daily_cost_for_instance("VM.Unknown.Shape", 1.0, "running") == 0.0

    def test_daily_cost_gpu_shape(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_instance

        cost = _daily_cost_for_instance("VM.GPU2.1", 1.0, "running")
        assert cost == pytest.approx(2.95 * 24, rel=1e-4)

    def test_daily_cost_block_volume_balanced(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_block_volume

        cost = _daily_cost_for_block_volume(100, 10)
        assert cost == pytest.approx(100 * 0.0255 / 30, rel=1e-4)

    def test_daily_cost_block_volume_lower_cost(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_block_volume

        cost = _daily_cost_for_block_volume(50, 0)
        assert cost == pytest.approx(50 * 0.0170 / 30, rel=1e-4)

    def test_daily_cost_block_volume_higher_perf(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_block_volume

        cost = _daily_cost_for_block_volume(200, 20)
        assert cost == pytest.approx(200 * 0.0340 / 30, rel=1e-4)

    def test_daily_cost_block_volume_ultra(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_block_volume

        cost = _daily_cost_for_block_volume(100, 30)
        assert cost == pytest.approx(100 * 0.0510 / 30, rel=1e-4)

    def test_daily_cost_lb_100mbps(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_lb

        assert _daily_cost_for_lb(100) == pytest.approx(0.01 * 24, rel=1e-4)

    def test_daily_cost_lb_flexible(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_lb

        assert _daily_cost_for_lb(0) == pytest.approx(0.014 * 24, rel=1e-4)

    def test_daily_cost_lb_400mbps(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_lb

        assert _daily_cost_for_lb(400) == pytest.approx(0.04 * 24, rel=1e-4)

    def test_daily_cost_oke_basic_free(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_oke

        assert _daily_cost_for_oke("basic") == 0.0

    def test_daily_cost_oke_enhanced(self) -> None:
        from cloud.oci.resource_collector import _daily_cost_for_oke

        assert _daily_cost_for_oke("enhanced") == pytest.approx(0.10 * 24, rel=1e-4)


# ---------------------------------------------------------------------------
# OCICostCollector
# ---------------------------------------------------------------------------


class TestOCICostCollector:
    def _make_cost_collector(self) -> Any:
        from cloud.oci.cost_collector import OCICostCollector

        c = OCICostCollector.__new__(OCICostCollector)
        c._tenancy_id = "ocid1.tenancy.oc1..test"
        return c

    def test_collect_costs_basic(self) -> None:
        collector = self._make_cost_collector()

        item1 = MagicMock()
        item1.computed_amount = 150.00
        item1.service = "COMPUTE"
        item1.region = "us-ashburn-1"
        item1.time_usage_started = "2025-06-01T00:00:00Z"

        item2 = MagicMock()
        item2.computed_amount = 45.50
        item2.service = "BLOCK_STORAGE"
        item2.region = "eu-frankfurt-1"
        item2.time_usage_started = "2025-06-01T00:00:00Z"

        item3 = MagicMock()
        item3.computed_amount = 0.0
        item3.service = "FREE_TIER"
        item3.region = "us-ashburn-1"
        item3.time_usage_started = "2025-06-01T00:00:00Z"

        result = MagicMock()
        result.data.items = [item1, item2, item3]

        mock_client = MagicMock()
        mock_client.request_summarized_usages.return_value = result
        collector._client = mock_client

        with patch.dict("sys.modules", {
            "oci": MagicMock(),
            "oci.usage_api": MagicMock(),
            "oci.usage_api.models": MagicMock(),
        }):
            snapshots = collector.collect_costs(date(2025, 6, 1), date(2025, 6, 30))

        # Zero-cost row should be excluded
        assert len(snapshots) == 2
        compute = snapshots[0]
        assert compute.provider == "oci"
        assert compute.account_id == "ocid1.tenancy.oc1..test"
        assert compute.service == "COMPUTE"
        assert compute.region == "us-ashburn-1"
        assert compute.cost_usd == pytest.approx(150.00)
        assert compute.period_start == date(2025, 6, 1)

    def test_collect_costs_empty(self) -> None:
        collector = self._make_cost_collector()

        result = MagicMock()
        result.data.items = []

        mock_client = MagicMock()
        mock_client.request_summarized_usages.return_value = result
        collector._client = mock_client

        with patch.dict("sys.modules", {
            "oci": MagicMock(),
            "oci.usage_api": MagicMock(),
            "oci.usage_api.models": MagicMock(),
        }):
            snapshots = collector.collect_costs(date(2025, 6, 1), date(2025, 6, 30))

        assert snapshots == []


# ---------------------------------------------------------------------------
# OCIResourceCollector — Compute Instances
# ---------------------------------------------------------------------------


class TestOCIResourceCollectorInstances:
    def test_collect_running_flex_instance(self) -> None:
        collector = _make_collector()

        mock_inst = MagicMock()
        mock_inst.id = "ocid1.instance.oc1..test"
        mock_inst.display_name = "web-server-1"
        mock_inst.region = "us-ashburn-1"
        mock_inst.shape = "VM.Standard.E4.Flex"
        mock_inst.lifecycle_state = "RUNNING"
        mock_inst.availability_domain = "AD-1"
        mock_inst.fault_domain = "FD-1"
        mock_inst.freeform_tags = {"env": "prod"}
        shape_config = MagicMock()
        shape_config.ocpus = 4
        shape_config.memory_in_gbs = 64
        mock_inst.shape_config = shape_config

        mock_pagination_result = MagicMock()
        mock_pagination_result.data = [mock_inst]

        mock_oci = MagicMock()
        mock_oci.pagination.list_call_get_all_results.return_value = mock_pagination_result

        with patch.dict("sys.modules", {"oci": mock_oci, "oci.core": mock_oci.core}):
            snapshots = collector._collect_instances()

        assert len(snapshots) == 1
        s = snapshots[0]
        assert s.provider == "oci"
        assert s.service == "Compute"
        assert s.type == "compute"
        assert s.state == "running"
        assert s.daily_cost > 0
        assert s.metadata["shape"] == "VM.Standard.E4.Flex"
        assert s.metadata["ocpus"] == 4
        assert s.tags == {"env": "prod"}

    def test_collect_stopped_instance_zero_cost(self) -> None:
        collector = _make_collector()

        mock_inst = MagicMock()
        mock_inst.id = "ocid1.instance.oc1..test2"
        mock_inst.display_name = "stopped-vm"
        mock_inst.region = "us-ashburn-1"
        mock_inst.shape = "VM.Standard.E4.Flex"
        mock_inst.lifecycle_state = "STOPPED"
        mock_inst.availability_domain = "AD-1"
        mock_inst.fault_domain = "FD-1"
        mock_inst.freeform_tags = {}
        shape_config = MagicMock()
        shape_config.ocpus = 2
        shape_config.memory_in_gbs = 32
        mock_inst.shape_config = shape_config

        mock_pagination_result = MagicMock()
        mock_pagination_result.data = [mock_inst]

        mock_oci = MagicMock()
        mock_oci.pagination.list_call_get_all_results.return_value = mock_pagination_result

        with patch.dict("sys.modules", {"oci": mock_oci, "oci.core": mock_oci.core}):
            snapshots = collector._collect_instances()

        assert snapshots[0].daily_cost == 0.0
        assert snapshots[0].state == "stopped"

    def test_terminated_instances_excluded(self) -> None:
        collector = _make_collector()

        mock_inst = MagicMock()
        mock_inst.id = "ocid1.instance.oc1..term"
        mock_inst.display_name = "terminated-vm"
        mock_inst.lifecycle_state = "TERMINATED"
        mock_inst.freeform_tags = {}

        mock_pagination_result = MagicMock()
        mock_pagination_result.data = [mock_inst]

        mock_oci = MagicMock()
        mock_oci.pagination.list_call_get_all_results.return_value = mock_pagination_result

        with patch.dict("sys.modules", {"oci": mock_oci, "oci.core": mock_oci.core}):
            snapshots = collector._collect_instances()

        assert len(snapshots) == 0


# ---------------------------------------------------------------------------
# OCIResourceCollector — Block Volumes
# ---------------------------------------------------------------------------


class TestOCIResourceCollectorBlockVolumes:
    def test_unattached_volume(self) -> None:
        collector = _make_collector()

        mock_vol = MagicMock()
        mock_vol.id = "ocid1.volume.oc1..test"
        mock_vol.display_name = "data-vol"
        mock_vol.lifecycle_state = "AVAILABLE"
        mock_vol.size_in_gbs = 100
        mock_vol.vpus_per_gb = 10
        mock_vol.availability_domain = "AD-1"
        mock_vol.freeform_tags = {}

        mock_vol_result = MagicMock()
        mock_vol_result.data = [mock_vol]

        # No attachments → volume is unattached
        mock_attach_result = MagicMock()
        mock_attach_result.data = []

        mock_oci = MagicMock()
        mock_oci.pagination.list_call_get_all_results.side_effect = [
            mock_vol_result,
            mock_attach_result,
        ]

        with patch.dict("sys.modules", {"oci": mock_oci, "oci.core": mock_oci.core}):
            snapshots = collector._collect_block_volumes()

        assert len(snapshots) == 1
        s = snapshots[0]
        assert s.state == "unattached"
        assert s.service == "BlockVolume"
        assert s.type == "storage"
        assert s.daily_cost > 0

    def test_attached_volume(self) -> None:
        collector = _make_collector()

        mock_vol = MagicMock()
        mock_vol.id = "ocid1.volume.oc1..attached"
        mock_vol.display_name = "boot-vol"
        mock_vol.lifecycle_state = "AVAILABLE"
        mock_vol.size_in_gbs = 50
        mock_vol.vpus_per_gb = 10
        mock_vol.availability_domain = "AD-1"
        mock_vol.freeform_tags = {}

        mock_vol_result = MagicMock()
        mock_vol_result.data = [mock_vol]

        mock_attachment = MagicMock()
        mock_attachment.volume_id = "ocid1.volume.oc1..attached"
        mock_attachment.lifecycle_state = "ATTACHED"
        mock_attach_result = MagicMock()
        mock_attach_result.data = [mock_attachment]

        mock_oci = MagicMock()
        mock_oci.pagination.list_call_get_all_results.side_effect = [
            mock_vol_result,
            mock_attach_result,
        ]

        with patch.dict("sys.modules", {"oci": mock_oci, "oci.core": mock_oci.core}):
            snapshots = collector._collect_block_volumes()

        assert snapshots[0].state == "attached"


# ---------------------------------------------------------------------------
# OCIResourceCollector — Load Balancers
# ---------------------------------------------------------------------------


class TestOCIResourceCollectorLBs:
    def test_collect_active_lb(self) -> None:
        collector = _make_collector()

        mock_lb = MagicMock()
        mock_lb.id = "ocid1.loadbalancer.oc1..test"
        mock_lb.display_name = "web-lb"
        mock_lb.lifecycle_state = "ACTIVE"
        mock_lb.shape_name = "flexible"
        mock_lb.freeform_tags = {"team": "platform"}
        mock_lb.backend_sets = {"bs1": MagicMock(), "bs2": MagicMock()}

        mock_pagination_result = MagicMock()
        mock_pagination_result.data = [mock_lb]

        mock_oci = MagicMock()
        mock_oci.pagination.list_call_get_all_results.return_value = mock_pagination_result

        with patch.dict("sys.modules", {
            "oci": mock_oci,
            "oci.load_balancer": mock_oci.load_balancer,
        }):
            snapshots = collector._collect_load_balancers()

        assert len(snapshots) == 1
        s = snapshots[0]
        assert s.service == "LoadBalancer"
        assert s.type == "network"
        assert s.state == "active"
        assert s.metadata["backend_set_count"] == 2
        assert s.tags == {"team": "platform"}


# ---------------------------------------------------------------------------
# OCIResourceCollector — OKE
# ---------------------------------------------------------------------------


class TestOCIResourceCollectorOKE:
    def test_collect_enhanced_cluster_with_pool(self) -> None:
        collector = _make_collector()

        mock_cluster = MagicMock()
        mock_cluster.id = "ocid1.cluster.oc1..test"
        mock_cluster.name = "prod-cluster"
        mock_cluster.lifecycle_state = "ACTIVE"
        mock_cluster.kubernetes_version = "v1.29.1"
        mock_cluster.vcn_id = "ocid1.vcn.oc1..test"
        mock_cluster.freeform_tags = {}
        mock_cluster.cluster_pod_network_options = [MagicMock()]  # enhanced

        mock_pool = MagicMock()
        mock_pool.id = "ocid1.nodepool.oc1..test"
        mock_pool.name = "default-pool"
        mock_pool.lifecycle_state = "ACTIVE"
        mock_pool.node_shape = "VM.Standard.E4.Flex"
        mock_pool.kubernetes_version = "v1.29.1"
        shape_config = MagicMock()
        shape_config.ocpus = 2
        mock_pool.node_shape_config = shape_config
        node_config = MagicMock()
        node_config.size = 3
        mock_pool.node_config_details = node_config

        mock_cluster_result = MagicMock()
        mock_cluster_result.data = [mock_cluster]

        mock_pool_result = MagicMock()
        mock_pool_result.data = [mock_pool]

        mock_oci = MagicMock()
        mock_oci.pagination.list_call_get_all_results.side_effect = [
            mock_cluster_result,
            mock_pool_result,
        ]

        with patch.dict("sys.modules", {
            "oci": mock_oci,
            "oci.container_engine": mock_oci.container_engine,
        }):
            snapshots = collector._collect_oke()

        assert len(snapshots) == 2  # cluster + pool
        cluster_snap = snapshots[0]
        assert cluster_snap.service == "OKE"
        assert cluster_snap.metadata["cluster_type"] == "enhanced"
        assert cluster_snap.daily_cost == pytest.approx(0.10 * 24, rel=1e-4)

        pool_snap = snapshots[1]
        assert pool_snap.name == "prod-cluster/default-pool"
        assert pool_snap.daily_cost > 0

    def test_basic_cluster_zero_control_plane_cost(self) -> None:
        collector = _make_collector()

        mock_cluster = MagicMock()
        mock_cluster.id = "ocid1.cluster.oc1..basic"
        mock_cluster.name = "dev-cluster"
        mock_cluster.lifecycle_state = "ACTIVE"
        mock_cluster.kubernetes_version = "v1.28.0"
        mock_cluster.vcn_id = "ocid1.vcn.oc1..dev"
        mock_cluster.freeform_tags = {}
        mock_cluster.cluster_pod_network_options = None  # basic

        mock_cluster_result = MagicMock()
        mock_cluster_result.data = [mock_cluster]
        mock_pool_result = MagicMock()
        mock_pool_result.data = []

        mock_oci = MagicMock()
        mock_oci.pagination.list_call_get_all_results.side_effect = [
            mock_cluster_result,
            mock_pool_result,
        ]

        with patch.dict("sys.modules", {
            "oci": mock_oci,
            "oci.container_engine": mock_oci.container_engine,
        }):
            snapshots = collector._collect_oke()

        assert snapshots[0].daily_cost == 0.0


# ---------------------------------------------------------------------------
# OCICollector — config building
# ---------------------------------------------------------------------------


class TestOCICollectorConfig:
    def test_builds_config_from_default(self) -> None:
        mock_oci = MagicMock()
        mock_oci.config.from_file.return_value = {
            "tenancy": "ocid1.tenancy.oc1..test",
            "region": "us-ashburn-1",
        }

        with patch.dict("sys.modules", {"oci": mock_oci, "oci.config": mock_oci.config}):
            from cloud.oci.collector import _build_config

            cfg = _build_config(config_file=None, profile=None)

        mock_oci.config.from_file.assert_called_once_with(profile_name="DEFAULT")
        assert cfg["tenancy"] == "ocid1.tenancy.oc1..test"

    def test_builds_config_with_custom_file(self) -> None:
        mock_oci = MagicMock()
        mock_oci.config.from_file.return_value = {
            "tenancy": "ocid1.tenancy.oc1..custom",
            "region": "eu-frankfurt-1",
        }

        with patch.dict("sys.modules", {"oci": mock_oci, "oci.config": mock_oci.config}):
            from cloud.oci.collector import _build_config

            cfg = _build_config(config_file="/custom/oci/config", profile="PROD")

        mock_oci.config.from_file.assert_called_once_with(
            file_location="/custom/oci/config",
            profile_name="PROD",
        )
        assert cfg["tenancy"] == "ocid1.tenancy.oc1..custom"


# ---------------------------------------------------------------------------
# Integration placeholder
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_oci_collector_live() -> None:
    """Requires real OCI credentials and Usage API access."""
    ...
