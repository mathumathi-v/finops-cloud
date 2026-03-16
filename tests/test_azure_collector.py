# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for Azure collector modules (no real Azure credentials required)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_collector() -> Any:
    """Return an AzureResourceCollector with mocked credentials."""
    from cloud.azure.resource_collector import AzureResourceCollector

    c = AzureResourceCollector.__new__(AzureResourceCollector)
    c._subscription_id = "sub-test-1234"
    c._credential = MagicMock()
    c._snapshot_time = datetime(2025, 6, 1, tzinfo=UTC)
    return c


# ---------------------------------------------------------------------------
# Helpers — pure functions
# ---------------------------------------------------------------------------


class TestAzureResourceHelpers:
    def test_parse_resource_group(self) -> None:
        from cloud.azure.resource_collector import _parse_resource_group

        rid = "/subscriptions/abc/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachines/vm1"
        assert _parse_resource_group(rid) == "my-rg"

    def test_parse_resource_group_missing(self) -> None:
        from cloud.azure.resource_collector import _parse_resource_group

        assert _parse_resource_group("/subscriptions/abc") == ""

    def test_daily_cost_running_vm(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_vm

        cost = _daily_cost_for_vm("Standard_D2s_v3", "running")
        assert cost == pytest.approx(0.096 * 24)

    def test_daily_cost_deallocated_vm_zero(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_vm

        assert _daily_cost_for_vm("Standard_D4s_v3", "deallocated") == 0.0

    def test_daily_cost_stopped_vm_zero(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_vm

        assert _daily_cost_for_vm("Standard_D4s_v3", "stopped") == 0.0

    def test_daily_cost_unknown_vm_size(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_vm

        assert _daily_cost_for_vm("Standard_M128s", "running") == 0.0

    def test_daily_cost_premium_disk(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_disk

        cost = _daily_cost_for_disk("Premium_LRS", 128)
        assert cost == pytest.approx(128 * 0.135 / 30, rel=1e-4)

    def test_daily_cost_standard_disk(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_disk

        cost = _daily_cost_for_disk("Standard_LRS", 100)
        assert cost == pytest.approx(100 * 0.04 / 30, rel=1e-4)


# ---------------------------------------------------------------------------
# AzureCostCollector
# ---------------------------------------------------------------------------


class TestAzureCostCollector:
    def _make_cost_collector(self) -> Any:
        from cloud.azure.cost_collector import AzureCostCollector

        c = AzureCostCollector.__new__(AzureCostCollector)
        c._subscription_id = "sub-test-1234"
        c._scope = "/subscriptions/sub-test-1234"
        return c

    def _make_result(self, rows: list[list[Any]]) -> MagicMock:
        result = MagicMock()
        result.columns = [
            MagicMock(name="Cost"),
            MagicMock(name="UsageDate"),
            MagicMock(name="ServiceName"),
            MagicMock(name="ResourceLocation"),
            MagicMock(name="Currency"),
        ]
        # Fix: set .name attribute properly on column mocks
        result.columns[0].name = "Cost"
        result.columns[1].name = "UsageDate"
        result.columns[2].name = "ServiceName"
        result.columns[3].name = "ResourceLocation"
        result.columns[4].name = "Currency"
        result.rows = rows
        return result

    def test_collect_costs_basic(self) -> None:
        collector = self._make_cost_collector()
        mock_client = MagicMock()
        mock_client.query.usage.return_value = self._make_result([
            [150.00, "20250601", "Virtual Machines", "eastus", "USD"],
            [45.50,  "20250601", "Azure SQL",        "westeurope", "USD"],
            [0.00,   "20250601", "Free Service",     "eastus", "USD"],
        ])
        collector._client = mock_client

        with patch("cloud.azure.cost_collector.AzureCostCollector.collect_costs",
                   wraps=collector.collect_costs):
            # Patch the imports inside the method
            with patch.dict("sys.modules", {
                "azure.mgmt.costmanagement.models": MagicMock(
                    QueryDefinition=MagicMock(),
                    QueryDataset=MagicMock(),
                    QueryGrouping=MagicMock(),
                    QueryTimePeriod=MagicMock(),
                    QueryAggregation=MagicMock(),
                )
            }):
                snapshots = collector.collect_costs(date(2025, 6, 1), date(2025, 6, 30))

        # Zero-cost row should be excluded
        assert len(snapshots) == 2
        vm = snapshots[0]
        assert vm.provider == "azure"
        assert vm.account_id == "sub-test-1234"
        assert vm.service == "Virtual Machines"
        assert vm.region == "eastus"
        assert vm.cost_usd == pytest.approx(150.00)
        assert vm.period_start == date(2025, 6, 1)

    def test_collect_costs_empty(self) -> None:
        collector = self._make_cost_collector()
        mock_client = MagicMock()
        mock_client.query.usage.return_value = self._make_result([])
        collector._client = mock_client

        with patch.dict("sys.modules", {
            "azure.mgmt.costmanagement.models": MagicMock(
                QueryDefinition=MagicMock(),
                QueryDataset=MagicMock(),
                QueryGrouping=MagicMock(),
                QueryTimePeriod=MagicMock(),
                QueryAggregation=MagicMock(),
            )
        }):
            snapshots = collector.collect_costs(date(2025, 6, 1), date(2025, 6, 30))
        assert snapshots == []

    def test_region_normalized_to_lowercase(self) -> None:
        collector = self._make_cost_collector()
        mock_client = MagicMock()
        mock_client.query.usage.return_value = self._make_result([
            [10.0, "20250601", "Storage", "East US", "USD"],
        ])
        collector._client = mock_client

        with patch.dict("sys.modules", {
            "azure.mgmt.costmanagement.models": MagicMock(
                QueryDefinition=MagicMock(),
                QueryDataset=MagicMock(),
                QueryGrouping=MagicMock(),
                QueryTimePeriod=MagicMock(),
                QueryAggregation=MagicMock(),
            )
        }):
            snapshots = collector.collect_costs(date(2025, 6, 1), date(2025, 6, 30))

        assert snapshots[0].region == "east-us"


# ---------------------------------------------------------------------------
# AzureResourceCollector — VMs
# ---------------------------------------------------------------------------


def _make_vm(
    name: str = "test-vm",
    location: str = "eastus",
    vm_size: str = "Standard_D2s_v3",
    power_state: str = "running",
    tags: dict[str, str] | None = None,
    resource_id: str = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/test-vm",
) -> MagicMock:
    vm = MagicMock()
    vm.name = name
    vm.id = resource_id
    vm.location = location
    vm.tags = tags or {}
    hw = MagicMock()
    hw.vm_size = vm_size
    vm.hardware_profile = hw
    sp = MagicMock()
    sp.os_disk.os_type = "Linux"
    vm.storage_profile = sp
    return vm


class TestAzureResourceCollectorVMs:
    def test_collect_vms_running(self) -> None:
        collector = _make_collector()

        mock_vm = _make_vm(power_state="running")
        mock_iv = MagicMock()
        status = MagicMock()
        status.code = "PowerState/running"
        mock_iv.statuses = [status]
        mock_iv_vm = MagicMock()
        mock_iv_vm.instance_view = mock_iv

        mock_client = MagicMock()
        mock_client.virtual_machines.list_all.return_value = [mock_vm]
        mock_client.virtual_machines.get.return_value = mock_iv_vm

        with patch("azure.mgmt.compute.ComputeManagementClient", return_value=mock_client):
            snapshots = collector._collect_vms()

        assert len(snapshots) == 1
        s = snapshots[0]
        assert s.provider == "azure"
        assert s.service == "VirtualMachine"
        assert s.type == "compute"
        assert s.state == "running"
        assert s.daily_cost > 0

    def test_collect_vms_deallocated_zero_cost(self) -> None:
        collector = _make_collector()

        mock_vm = _make_vm()
        mock_iv = MagicMock()
        status = MagicMock()
        status.code = "PowerState/deallocated"
        mock_iv.statuses = [status]
        mock_iv_vm = MagicMock()
        mock_iv_vm.instance_view = mock_iv

        mock_client = MagicMock()
        mock_client.virtual_machines.list_all.return_value = [mock_vm]
        mock_client.virtual_machines.get.return_value = mock_iv_vm

        with patch("azure.mgmt.compute.ComputeManagementClient", return_value=mock_client):
            snapshots = collector._collect_vms()

        assert snapshots[0].daily_cost == 0.0
        assert snapshots[0].state == "stopped"


# ---------------------------------------------------------------------------
# AzureResourceCollector — Disks
# ---------------------------------------------------------------------------


def _make_disk(
    name: str = "test-disk",
    location: str = "eastus",
    sku_name: str = "Premium_LRS",
    size_gb: int = 128,
    disk_state: str = "Unattached",
) -> MagicMock:
    disk = MagicMock()
    disk.name = name
    disk.id = f"/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/disks/{name}"
    disk.location = location
    disk.sku = MagicMock(name=sku_name)
    disk.disk_size_gb = size_gb
    disk.disk_state = disk_state
    disk.tags = {}
    disk.os_type = None
    return disk


class TestAzureResourceCollectorDisks:
    def test_unattached_disk(self) -> None:
        collector = _make_collector()

        mock_disk = _make_disk(disk_state="Unattached")
        mock_client = MagicMock()
        mock_client.disks.list.return_value = [mock_disk]

        with patch("azure.mgmt.compute.ComputeManagementClient", return_value=mock_client):
            snapshots = collector._collect_disks()

        assert len(snapshots) == 1
        s = snapshots[0]
        assert s.state == "unattached"
        assert s.service == "ManagedDisk"
        assert s.type == "storage"
        assert s.daily_cost > 0

    def test_attached_disk(self) -> None:
        collector = _make_collector()

        mock_disk = _make_disk(disk_state="Attached")
        mock_client = MagicMock()
        mock_client.disks.list.return_value = [mock_disk]

        with patch("azure.mgmt.compute.ComputeManagementClient", return_value=mock_client):
            snapshots = collector._collect_disks()

        assert snapshots[0].state == "attached"


# ---------------------------------------------------------------------------
# AzureResourceCollector — Load Balancers
# ---------------------------------------------------------------------------


class TestAzureResourceCollectorLBs:
    def test_collect_load_balancers(self) -> None:
        collector = _make_collector()

        mock_lb = MagicMock()
        mock_lb.name = "my-lb"
        mock_lb.id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network/loadBalancers/my-lb"
        mock_lb.location = "westeurope"
        mock_lb.sku.name = "Standard"
        mock_lb.tags = {}
        mock_lb.frontend_ip_configurations = [MagicMock(), MagicMock()]

        mock_client = MagicMock()
        mock_client.load_balancers.list_all.return_value = [mock_lb]

        with patch("azure.mgmt.network.NetworkManagementClient", return_value=mock_client):
            snapshots = collector._collect_load_balancers()

        assert len(snapshots) == 1
        s = snapshots[0]
        assert s.service == "LoadBalancer"
        assert s.type == "network"
        assert s.region == "westeurope"
        assert s.metadata["frontend_count"] == 2


# ---------------------------------------------------------------------------
# AzureCollector — credential building
# ---------------------------------------------------------------------------


class TestAzureCollectorCredentials:
    def test_builds_client_secret_credential_when_all_provided(self) -> None:
        with patch("azure.identity.ClientSecretCredential") as mock_csc:
            from cloud.azure.collector import _build_credential
            _build_credential("tenant-id", "client-id", "client-secret")
            mock_csc.assert_called_once_with(
                tenant_id="tenant-id",
                client_id="client-id",
                client_secret="client-secret",
            )

    def test_builds_default_credential_when_incomplete(self) -> None:
        with patch("azure.identity.DefaultAzureCredential") as mock_dac:
            from cloud.azure.collector import _build_credential
            _build_credential(None, None, None)
            mock_dac.assert_called_once()


# ---------------------------------------------------------------------------
# Integration placeholder
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_azure_collector_live() -> None:
    """Requires real Azure credentials and Cost Management Reader role."""
    ...
