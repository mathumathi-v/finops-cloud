# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for Azure collector modules (no real Azure credentials required)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("azure.identity", reason="azure-identity not installed")

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

    def test_daily_cost_for_lb_standard(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_lb

        assert _daily_cost_for_lb("Standard") == pytest.approx(0.025 * 24, rel=1e-4)

    def test_daily_cost_for_lb_basic_free(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_lb

        assert _daily_cost_for_lb("Basic") == 0.0

    def test_daily_cost_for_lb_gateway(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_lb

        assert _daily_cost_for_lb("Gateway") == pytest.approx(0.014 * 24, rel=1e-4)

    def test_daily_cost_aks_standard_tier(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_aks_control_plane

        assert _daily_cost_for_aks_control_plane("Standard") == pytest.approx(0.10 * 24, rel=1e-4)

    def test_daily_cost_aks_free_tier(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_aks_control_plane

        assert _daily_cost_for_aks_control_plane("Free") == 0.0

    def test_daily_cost_app_service_plan_s1(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_app_service_plan

        assert _daily_cost_for_app_service_plan("S1") == pytest.approx(2.40)

    def test_daily_cost_app_service_plan_f1_free(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_app_service_plan

        assert _daily_cost_for_app_service_plan("F1") == 0.0

    def test_daily_cost_app_service_plan_unknown(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_app_service_plan

        assert _daily_cost_for_app_service_plan("X99") == 0.0

    def test_expanded_vm_sizes(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_vm

        # D-series v3 (no 's')
        assert _daily_cost_for_vm("Standard_D2_v3", "running") == pytest.approx(0.096 * 24, rel=1e-4)
        # E-series memory-optimised
        assert _daily_cost_for_vm("Standard_E8s_v3", "running") == pytest.approx(0.504 * 24, rel=1e-4)
        # GPU NC-series
        assert _daily_cost_for_vm("Standard_NC6", "running") == pytest.approx(0.90 * 24, rel=1e-4)
        # A-series
        assert _daily_cost_for_vm("Standard_A2_v2", "running") == pytest.approx(0.085 * 24, rel=1e-4)


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
    def test_collect_load_balancers_standard(self) -> None:
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
        # Standard LB should have non-zero daily cost
        assert s.daily_cost == pytest.approx(0.025 * 24, rel=1e-4)
        assert s.monthly_cost_estimate == pytest.approx(0.025 * 24 * 30, rel=1e-4)

    def test_collect_load_balancers_basic_free(self) -> None:
        collector = _make_collector()

        mock_lb = MagicMock()
        mock_lb.name = "basic-lb"
        mock_lb.id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network/loadBalancers/basic-lb"
        mock_lb.location = "eastus"
        mock_lb.sku.name = "Basic"
        mock_lb.tags = {}
        mock_lb.frontend_ip_configurations = [MagicMock()]

        mock_client = MagicMock()
        mock_client.load_balancers.list_all.return_value = [mock_lb]

        with patch("azure.mgmt.network.NetworkManagementClient", return_value=mock_client):
            snapshots = collector._collect_load_balancers()

        assert snapshots[0].daily_cost == 0.0

    def test_collect_load_balancers_gateway(self) -> None:
        collector = _make_collector()

        mock_lb = MagicMock()
        mock_lb.name = "gw-lb"
        mock_lb.id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network/loadBalancers/gw-lb"
        mock_lb.location = "eastus"
        mock_lb.sku.name = "Gateway"
        mock_lb.tags = {}
        mock_lb.frontend_ip_configurations = []

        mock_client = MagicMock()
        mock_client.load_balancers.list_all.return_value = [mock_lb]

        with patch("azure.mgmt.network.NetworkManagementClient", return_value=mock_client):
            snapshots = collector._collect_load_balancers()

        assert snapshots[0].daily_cost == pytest.approx(0.014 * 24, rel=1e-4)


# ---------------------------------------------------------------------------
# AzureResourceCollector — AKS
# ---------------------------------------------------------------------------


class TestAzureResourceCollectorAKS:
    def test_aks_standard_tier_has_control_plane_cost(self) -> None:
        collector = _make_collector()

        mock_cluster = MagicMock()
        mock_cluster.name = "my-cluster"
        mock_cluster.id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.ContainerService/managedClusters/my-cluster"
        mock_cluster.location = "eastus"
        mock_cluster.tags = {}
        mock_cluster.provisioning_state = "Succeeded"
        mock_cluster.kubernetes_version = "1.29.0"
        mock_cluster.node_resource_group = "MC_rg_my-cluster_eastus"
        mock_cluster.dns_prefix = "my-cluster"
        mock_cluster.sku = MagicMock()
        mock_cluster.sku.tier = "Standard"
        mock_cluster.agent_pool_profiles = []

        mock_client = MagicMock()
        mock_client.managed_clusters.list.return_value = [mock_cluster]

        with patch("azure.mgmt.containerservice.ContainerServiceClient", return_value=mock_client):
            snapshots = collector._collect_aks()

        assert len(snapshots) == 1
        assert snapshots[0].daily_cost == pytest.approx(0.10 * 24, rel=1e-4)
        assert snapshots[0].metadata["sku_tier"] == "Standard"

    def test_aks_free_tier_zero_control_plane_cost(self) -> None:
        collector = _make_collector()

        mock_cluster = MagicMock()
        mock_cluster.name = "free-cluster"
        mock_cluster.id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.ContainerService/managedClusters/free-cluster"
        mock_cluster.location = "eastus"
        mock_cluster.tags = {}
        mock_cluster.provisioning_state = "Succeeded"
        mock_cluster.kubernetes_version = "1.29.0"
        mock_cluster.node_resource_group = ""
        mock_cluster.dns_prefix = "free-cluster"
        mock_cluster.sku = MagicMock()
        mock_cluster.sku.tier = "Free"
        mock_cluster.agent_pool_profiles = []

        mock_client = MagicMock()
        mock_client.managed_clusters.list.return_value = [mock_cluster]

        with patch("azure.mgmt.containerservice.ContainerServiceClient", return_value=mock_client):
            snapshots = collector._collect_aks()

        assert snapshots[0].daily_cost == 0.0


# ---------------------------------------------------------------------------
# AzureResourceCollector — Storage Accounts
# ---------------------------------------------------------------------------


class TestAzureResourceCollectorStorageAccounts:
    def test_collect_storage_accounts(self) -> None:
        collector = _make_collector()

        mock_account = MagicMock()
        mock_account.name = "mystorageacct"
        mock_account.id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/mystorageacct"
        mock_account.location = "eastus"
        mock_account.sku = MagicMock()
        mock_account.sku.name = "Standard_LRS"
        mock_account.kind = "StorageV2"
        mock_account.tags = {"env": "prod"}
        mock_account.access_tier = "Hot"
        mock_account.enable_https_traffic_only = True

        mock_storage_client = MagicMock()
        mock_storage_client.storage_accounts.list.return_value = [mock_account]

        mock_storage_module = MagicMock()
        mock_storage_module.StorageManagementClient.return_value = mock_storage_client

        with patch.dict("sys.modules", {"azure.mgmt.storage": mock_storage_module}):
            snapshots = collector._collect_storage_accounts()

        assert len(snapshots) == 1
        s = snapshots[0]
        assert s.service == "StorageAccount"
        assert s.type == "storage"
        assert s.provider == "azure"
        # Cost is 0.0 (data-dependent, unknown without monitoring)
        assert s.daily_cost == 0.0
        assert s.metadata["sku"] == "Standard_LRS"
        assert s.metadata["kind"] == "StorageV2"
        assert s.metadata["access_tier"] == "Hot"
        assert s.metadata["https_only"] is True
        assert s.tags == {"env": "prod"}


# ---------------------------------------------------------------------------
# AzureResourceCollector — App Service Plans
# ---------------------------------------------------------------------------


class TestAzureResourceCollectorAppServicePlans:
    def _make_mock_web_module(self, plan_mock: MagicMock) -> tuple[MagicMock, MagicMock]:
        mock_web_client = MagicMock()
        mock_web_client.app_service_plans.list.return_value = [plan_mock]
        mock_web_module = MagicMock()
        mock_web_module.WebSiteManagementClient.return_value = mock_web_client
        return mock_web_module, mock_web_client

    def test_collect_app_service_plans_standard_tier(self) -> None:
        collector = _make_collector()

        mock_plan = MagicMock()
        mock_plan.name = "my-asp"
        mock_plan.id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Web/serverfarms/my-asp"
        mock_plan.location = "eastus"
        mock_plan.sku = MagicMock()
        mock_plan.sku.name = "S1"
        mock_plan.sku.tier = "Standard"
        mock_plan.tags = {}
        mock_plan.number_of_sites = 3
        mock_plan.kind = "linux"

        mock_web_module, _ = self._make_mock_web_module(mock_plan)

        with patch.dict("sys.modules", {"azure.mgmt.web": mock_web_module}):
            snapshots = collector._collect_app_service_plans()

        assert len(snapshots) == 1
        s = snapshots[0]
        assert s.service == "AppServicePlan"
        assert s.type == "compute"
        assert s.daily_cost == pytest.approx(2.40)
        assert s.monthly_cost_estimate == pytest.approx(2.40 * 30, rel=1e-4)
        assert s.metadata["worker_count"] == 3
        assert s.metadata["sku_tier"] == "Standard"

    def test_collect_app_service_plans_free_tier(self) -> None:
        collector = _make_collector()

        mock_plan = MagicMock()
        mock_plan.name = "free-asp"
        mock_plan.id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Web/serverfarms/free-asp"
        mock_plan.location = "eastus"
        mock_plan.sku = MagicMock()
        mock_plan.sku.name = "F1"
        mock_plan.sku.tier = "Free"
        mock_plan.tags = {}
        mock_plan.number_of_sites = 1
        mock_plan.kind = "app"

        mock_web_module, _ = self._make_mock_web_module(mock_plan)

        with patch.dict("sys.modules", {"azure.mgmt.web": mock_web_module}):
            snapshots = collector._collect_app_service_plans()

        assert snapshots[0].daily_cost == 0.0


# ---------------------------------------------------------------------------
# AzureCollector — credential building
# ---------------------------------------------------------------------------


class TestAzureResourceCollectorSQLDatabases:
    def test_collect_sql_databases(self) -> None:
        collector = _make_collector()

        mock_server = MagicMock()
        mock_server.name = "sql-server-1"
        mock_server.id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Sql/servers/sql-server-1"
        mock_server.location = "eastus"

        mock_db = MagicMock()
        mock_db.name = "mydb"
        mock_db.id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Sql/servers/sql-server-1/databases/mydb"
        mock_db.location = "eastus"
        mock_db.sku = MagicMock()
        mock_db.sku.name = "S1"
        mock_db.sku.tier = "Standard"
        mock_db.tags = {}
        mock_db.status = "Online"
        mock_db.max_size_bytes = 268435456000
        mock_db.elastic_pool_id = None

        mock_sql_client = MagicMock()
        mock_sql_client.servers.list.return_value = [mock_server]
        mock_sql_client.databases.list_by_server.return_value = [mock_db]

        mock_sql_module = MagicMock()
        mock_sql_module.SqlManagementClient.return_value = mock_sql_client

        with patch.dict("sys.modules", {"azure.mgmt.sql": mock_sql_module}):
            snapshots = collector._collect_sql_databases()

        assert len(snapshots) == 1
        s = snapshots[0]
        assert s.type == "database"
        assert s.service == "AzureSQL"
        assert s.daily_cost > 0
        assert s.metadata["server_name"] == "sql-server-1"


class TestAzureResourceCollectorFunctionApps:
    def test_collect_function_apps(self) -> None:
        collector = _make_collector()

        mock_app = MagicMock()
        mock_app.name = "my-func-app"
        mock_app.id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Web/sites/my-func-app"
        mock_app.kind = "functionapp,linux"
        mock_app.location = "eastus"
        mock_app.state = "Running"
        mock_app.tags = {}
        mock_app.default_host_name = "my-func-app.azurewebsites.net"
        mock_app.https_only = True

        mock_web_client = MagicMock()
        mock_web_client.web_apps.list.return_value = [mock_app]
        mock_web_module = MagicMock()
        mock_web_module.WebSiteManagementClient.return_value = mock_web_client

        with patch.dict("sys.modules", {"azure.mgmt.web": mock_web_module}):
            snapshots = collector._collect_function_apps()

        assert len(snapshots) == 1
        s = snapshots[0]
        assert s.type == "serverless"
        assert s.service == "FunctionApp"

    def test_skips_non_function_apps(self) -> None:
        collector = _make_collector()

        mock_app = MagicMock()
        mock_app.kind = "app"  # Not a function app
        mock_app.name = "webapp"

        mock_web_client = MagicMock()
        mock_web_client.web_apps.list.return_value = [mock_app]
        mock_web_module = MagicMock()
        mock_web_module.WebSiteManagementClient.return_value = mock_web_client

        with patch.dict("sys.modules", {"azure.mgmt.web": mock_web_module}):
            snapshots = collector._collect_function_apps()

        assert len(snapshots) == 0


class TestAzureResourceCollectorCosmosDB:
    def test_collect_cosmos_db(self) -> None:
        collector = _make_collector()

        mock_account = MagicMock()
        mock_account.name = "my-cosmos"
        mock_account.id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.DocumentDB/databaseAccounts/my-cosmos"
        mock_account.location = "eastus"
        mock_account.tags = {}
        mock_account.kind = "GlobalDocumentDB"
        mock_account.consistency_policy = MagicMock()
        mock_account.consistency_policy.default_consistency_level = "Session"
        mock_account.enable_automatic_failover = True
        mock_account.database_account_offer_type = "Standard"

        mock_cosmos_client = MagicMock()
        mock_cosmos_client.database_accounts.list.return_value = [mock_account]
        mock_cosmos_module = MagicMock()
        mock_cosmos_module.CosmosDBManagementClient.return_value = mock_cosmos_client

        with patch.dict("sys.modules", {"azure.mgmt.cosmosdb": mock_cosmos_module}):
            snapshots = collector._collect_cosmos_db()

        assert len(snapshots) == 1
        s = snapshots[0]
        assert s.type == "database"
        assert s.service == "CosmosDB"


class TestAzureNewPricingHelpers:
    def test_daily_cost_sql_dtu_s1(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_sql_db

        assert _daily_cost_for_sql_db("S1") == pytest.approx(0.967)

    def test_daily_cost_sql_basic(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_sql_db

        assert _daily_cost_for_sql_db("Basic") == pytest.approx(0.1667)

    def test_daily_cost_vpn_gateway_vpngw1(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_vpn_gateway

        assert _daily_cost_for_vpn_gateway("VpnGw1") == pytest.approx(0.19 * 24, rel=1e-4)


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
