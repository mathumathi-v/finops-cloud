# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import datetime, timedelta, timezone

from cloud.pricing import PricingProvider
from cost_model.models import ResourceSnapshot
from intelligence.constants import IDLE_CPU_LOOKBACK_DAYS

logger = logging.getLogger(__name__)

# Azure VM size → approximate hourly on-demand USD (East US, Linux).
# Used only when billing data is not available per-resource.
_VM_HOURLY_USD: dict[str, float] = {
    # B-series (burstable)
    "standard_b1s": 0.0104,
    "standard_b1ms": 0.0207,
    "standard_b2s": 0.0416,
    "standard_b2ms": 0.0832,
    "standard_b4ms": 0.166,
    "standard_b8ms": 0.333,
    "standard_b12ms": 0.499,
    "standard_b16ms": 0.666,
    # D-series v3 (general purpose)
    "standard_d2_v3": 0.096,
    "standard_d4_v3": 0.192,
    "standard_d8_v3": 0.384,
    "standard_d16_v3": 0.768,
    "standard_d32_v3": 1.536,
    "standard_d2s_v3": 0.096,
    "standard_d4s_v3": 0.192,
    "standard_d8s_v3": 0.384,
    "standard_d16s_v3": 0.768,
    "standard_d32s_v3": 1.536,
    # D-series v4
    "standard_d2s_v4": 0.096,
    "standard_d4s_v4": 0.192,
    "standard_d8s_v4": 0.384,
    "standard_d16s_v4": 0.768,
    "standard_d2_v4": 0.096,
    "standard_d4_v4": 0.192,
    "standard_d8_v4": 0.384,
    "standard_d16_v4": 0.768,
    # D-series v5
    "standard_d2s_v5": 0.096,
    "standard_d4s_v5": 0.192,
    "standard_d8s_v5": 0.384,
    "standard_d16s_v5": 0.768,
    "standard_d2_v5": 0.096,
    "standard_d4_v5": 0.192,
    "standard_d8_v5": 0.384,
    "standard_d16_v5": 0.768,
    # Dас-series v4 (AMD)
    "standard_d2as_v4": 0.086,
    "standard_d4as_v4": 0.172,
    "standard_d8as_v4": 0.344,
    "standard_d16as_v4": 0.688,
    "standard_d32as_v4": 1.376,
    # E-series v3 (memory-optimised)
    "standard_e2s_v3": 0.126,
    "standard_e4s_v3": 0.252,
    "standard_e8s_v3": 0.504,
    "standard_e16s_v3": 1.008,
    "standard_e32s_v3": 2.016,
    # E-series v4
    "standard_e2s_v4": 0.126,
    "standard_e4s_v4": 0.252,
    "standard_e8s_v4": 0.504,
    "standard_e16s_v4": 1.008,
    # E-series v5
    "standard_e2s_v5": 0.126,
    "standard_e4s_v5": 0.252,
    "standard_e8s_v5": 0.504,
    "standard_e16s_v5": 1.008,
    # F-series v2 (compute-optimised)
    "standard_f2s_v2": 0.085,
    "standard_f4s_v2": 0.169,
    "standard_f8s_v2": 0.338,
    "standard_f16s_v2": 0.677,
    "standard_f32s_v2": 1.354,
    # F-series (original)
    "standard_f2": 0.094,
    "standard_f4": 0.188,
    "standard_f8": 0.376,
    "standard_f16": 0.751,
    # A-series v2 (entry-level)
    "standard_a1_v2": 0.043,
    "standard_a2_v2": 0.085,
    "standard_a4_v2": 0.17,
    "standard_a8_v2": 0.34,
    "standard_a2m_v2": 0.099,
    "standard_a4m_v2": 0.199,
    "standard_a8m_v2": 0.397,
    # NC-series (GPU — NVIDIA Tesla K80 / P100 / V100)
    "standard_nc6": 0.90,
    "standard_nc12": 1.80,
    "standard_nc24": 3.60,
    "standard_nc6s_v3": 3.06,
    "standard_nc12s_v3": 6.12,
    "standard_nc24s_v3": 12.24,
    "standard_nd40rs_v2": 22.032,
    # NV-series (GPU — visualisation)
    "standard_nv6": 1.14,
    "standard_nv12": 2.28,
    "standard_nv24": 4.56,
}

# Load Balancer hourly on-demand USD (East US).
# Basic SKU is free; Standard is ~$0.025/hr base; Gateway is ~$0.014/hr base.
_LB_HOURLY_USD: dict[str, float] = {
    "basic": 0.0,
    "standard": 0.025,
    "gateway": 0.014,
}

# AKS control-plane hourly USD.
# Free tier is $0.00; Standard and Premium tiers are $0.10/hr per cluster.
_AKS_CONTROL_PLANE_HOURLY_USD: dict[str, float] = {
    "free": 0.0,
    "standard": 0.10,
    "premium": 0.10,
}

# App Service Plan SKU → approximate daily USD (East US, Linux).
# Based on the cheapest SKU within each tier.
_APP_SERVICE_PLAN_DAILY_USD: dict[str, float] = {
    # Free / Shared
    "f1": 0.0,
    "d1": 0.316,
    # Basic
    "b1": 0.432,
    "b2": 0.864,
    "b3": 1.728,
    # Standard
    "s1": 2.40,
    "s2": 4.80,
    "s3": 9.60,
    # Premium v2
    "p1v2": 2.40,
    "p2v2": 4.80,
    "p3v2": 9.60,
    # Premium v3
    "p1v3": 4.056,
    "p2v3": 8.112,
    "p3v3": 16.176,
    # Isolated v1
    "i1": 24.0,
    "i2": 48.0,
    "i3": 96.0,
    # Isolated v2
    "i1v2": 24.0,
    "i2v2": 48.0,
    "i3v2": 96.0,
}

# Managed disk price per GB per month (LRS, East US)
_DISK_PRICE_PER_GB_MONTH: dict[str, float] = {
    "premium_lrs": 0.135,
    "standardssd_lrs": 0.075,
    "standard_lrs": 0.04,
    "ultrassd_lrs": 0.125,
    "premium_zrs": 0.17,
    "standardssd_zrs": 0.09,
}

# Azure SQL DTU-based daily USD (East US).
_SQL_DTU_DAILY_USD: dict[str, float] = {
    "basic": 0.1667,
    "s0": 0.4833, "s1": 0.967, "s2": 1.934, "s3": 3.867,
    "s4": 7.734, "s6": 15.468, "s7": 30.936, "s9": 61.872, "s12": 123.744,
    "p1": 15.0, "p2": 30.0, "p4": 60.0, "p6": 120.0, "p11": 240.0, "p15": 320.0,
}

# Azure SQL vCore-based hourly USD (General Purpose, East US).
_SQL_VCORE_HOURLY_USD: dict[str, float] = {
    "gp_gen5_2": 0.2084, "gp_gen5_4": 0.4168, "gp_gen5_8": 0.8336,
    "gp_gen5_16": 1.6672, "gp_gen5_32": 3.3344,
    "bc_gen5_2": 0.5002, "bc_gen5_4": 1.0004, "bc_gen5_8": 2.0008,
}

# VPN Gateway SKU hourly USD (East US).
_VPN_GATEWAY_HOURLY_USD: dict[str, float] = {
    "vpngw1": 0.19, "vpngw2": 0.49, "vpngw3": 1.25, "vpngw4": 1.60, "vpngw5": 3.22,
    "vpngw1az": 0.19, "vpngw2az": 0.49, "vpngw3az": 1.25,
    "basic": 0.04,
}

# Cosmos DB RU-based daily cost (East US) — per 100 RU/s.
_COSMOS_HOURLY_PER_100RU = 0.008

_HOURS_PER_DAY = 24.0
_DAYS_PER_MONTH = 30.0


def _daily_cost_for_vm(
    vm_size: str, power_state: str, hourly_override: float | None = None,
) -> float:
    """Estimate daily cost for a VM from its size and power state."""
    if "deallocated" in power_state.lower() or "stopped" in power_state.lower():
        return 0.0
    hourly = hourly_override if hourly_override is not None else _VM_HOURLY_USD.get(vm_size.lower(), 0.0)
    return round(hourly * _HOURS_PER_DAY, 6)


def _daily_cost_for_disk(sku: str, size_gb: int) -> float:
    """Estimate daily cost for a managed disk from SKU and size."""
    sku_key = sku.lower().replace(" ", "_").replace("-", "_")
    monthly_per_gb = _DISK_PRICE_PER_GB_MONTH.get(sku_key, 0.04)
    return round(size_gb * monthly_per_gb / _DAYS_PER_MONTH, 6)


def _daily_cost_for_lb(sku: str) -> float:
    """Estimate daily base cost for a load balancer from its SKU."""
    hourly = _LB_HOURLY_USD.get(sku.lower(), 0.0)
    return round(hourly * _HOURS_PER_DAY, 4)


def _daily_cost_for_aks_control_plane(tier: str) -> float:
    """Estimate daily control-plane cost for an AKS cluster tier."""
    hourly = _AKS_CONTROL_PLANE_HOURLY_USD.get(tier.lower(), 0.0)
    return round(hourly * _HOURS_PER_DAY, 4)


def _daily_cost_for_app_service_plan(sku_name: str) -> float:
    """Estimate daily cost for an App Service Plan SKU."""
    return _APP_SERVICE_PLAN_DAILY_USD.get(sku_name.lower(), 0.0)


def _daily_cost_for_sql_db(sku_name: str) -> float:
    """Estimate daily cost for an Azure SQL Database from its SKU."""
    key = sku_name.lower()
    # Check DTU-based first
    if key in _SQL_DTU_DAILY_USD:
        return _SQL_DTU_DAILY_USD[key]
    # Check vCore-based
    hourly = _SQL_VCORE_HOURLY_USD.get(key, 0.0)
    return round(hourly * _HOURS_PER_DAY, 4)


def _daily_cost_for_vpn_gateway(sku: str) -> float:
    """Estimate daily cost for a VPN Gateway from its SKU."""
    hourly = _VPN_GATEWAY_HOURLY_USD.get(sku.lower(), 0.0)
    return round(hourly * _HOURS_PER_DAY, 4)


def _parse_resource_group(resource_id: str) -> str:
    """Extract resource group name from an Azure resource ID."""
    parts = resource_id.lower().split("/")
    try:
        idx = parts.index("resourcegroups")
        return resource_id.split("/")[idx + 1]
    except (ValueError, IndexError):
        return ""


class AzureResourceCollector:
    """Fetches live Azure resource metadata."""

    def __init__(
        self,
        subscription_id: str,
        credential: object,
        pricing_provider: PricingProvider | None = None,
    ) -> None:
        """
        Args:
            subscription_id: Azure subscription ID.
            credential: An azure-identity credential object.
            pricing_provider: Optional dynamic pricing provider.
        """
        self._subscription_id = subscription_id
        self._credential = credential
        self._pricing = pricing_provider
        self._snapshot_time = datetime.now(timezone.utc)

    def collect_resources(self) -> list[ResourceSnapshot]:
        """Collect all supported Azure resources for the subscription."""
        self._snapshot_time = datetime.now(timezone.utc)
        snapshots: list[ResourceSnapshot] = []
        collectors = [
            ("VMs", self._collect_vms),
            ("Managed Disks", self._collect_disks),
            ("Load Balancers", self._collect_load_balancers),
            ("AKS clusters", self._collect_aks),
            ("Storage Accounts", self._collect_storage_accounts),
            ("App Service Plans", self._collect_app_service_plans),
            ("SQL Databases", self._collect_sql_databases),
            ("Function Apps", self._collect_function_apps),
            ("VPN Gateways", self._collect_vpn_gateways),
            ("CDN Profiles", self._collect_cdn_profiles),
            ("Cosmos DB Accounts", self._collect_cosmos_db),
        ]
        for name, fn in collectors:
            try:
                results = fn()
                snapshots.extend(results)
                logger.info("Collected %d Azure %s", len(results), name)
            except Exception:
                logger.warning(
                    "Failed to collect Azure %s (missing permission or provider not registered?)",
                    name,
                    exc_info=True,
                )
        logger.info(
            "Collected %d total Azure resources for subscription %s",
            len(snapshots),
            self._subscription_id,
        )
        return snapshots

    # ------------------------------------------------------------------
    # Virtual Machines
    # ------------------------------------------------------------------

    def _collect_vms(self) -> list[ResourceSnapshot]:
        """List all VMs in the subscription with instance view for power state."""
        try:
            from azure.mgmt.compute import ComputeManagementClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "azure-mgmt-compute is required. "
                "Install it with: pip install azure-mgmt-compute"
            ) from exc

        client = ComputeManagementClient(self._credential, self._subscription_id)  # type: ignore[arg-type]
        snapshots: list[ResourceSnapshot] = []

        for vm in client.virtual_machines.list_all():
            location = vm.location or "unknown"
            vm_size = vm.hardware_profile.vm_size if vm.hardware_profile else ""
            tags: dict[str, str] = dict(vm.tags or {})
            rg = _parse_resource_group(vm.id or "")

            # Get power state (requires instance view)
            power_state = "unknown"
            try:
                iv = client.virtual_machines.get(
                    resource_group_name=rg,
                    vm_name=vm.name or "",
                    expand="instanceView",
                ).instance_view
                if iv and iv.statuses:
                    for status in iv.statuses:
                        if status.code and status.code.startswith("PowerState/"):
                            power_state = status.code.split("/", 1)[1].lower()
                            break
            except Exception:
                logger.debug("Could not get instance view for VM %s", vm.name)

            # Normalize stopped states
            if power_state in ("stopped", "deallocated"):
                state = "stopped"
            elif power_state == "running":
                state = "running"
            else:
                state = power_state

            daily = _daily_cost_for_vm(vm_size or "", power_state)
            snapshots.append(
                ResourceSnapshot(
                    resource_id=vm.id or vm.name or "",
                    provider="azure",
                    account_id=self._subscription_id,
                    type="compute",
                    service="VirtualMachine",
                    name=vm.name or "",
                    region=location,
                    daily_cost=daily,
                    monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                    currency="USD",
                    state=state,
                    tags=tags,
                    metadata={
                        "vm_size": vm_size or "",
                        "resource_group": rg,
                        "os_type": (
                            vm.storage_profile.os_disk.os_type
                            if vm.storage_profile and vm.storage_profile.os_disk
                            else ""
                        ),
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # Managed Disks
    # ------------------------------------------------------------------

    def _collect_disks(self) -> list[ResourceSnapshot]:
        """List all managed disks in the subscription."""
        try:
            from azure.mgmt.compute import ComputeManagementClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "azure-mgmt-compute is required. "
                "Install it with: pip install azure-mgmt-compute"
            ) from exc

        client = ComputeManagementClient(self._credential, self._subscription_id)  # type: ignore[arg-type]
        snapshots: list[ResourceSnapshot] = []

        for disk in client.disks.list():
            location = disk.location or "unknown"
            sku = disk.sku.name if disk.sku else "Standard_LRS"
            size_gb = disk.disk_size_gb or 0
            tags: dict[str, str] = dict(disk.tags or {})
            rg = _parse_resource_group(disk.id or "")

            # disk_state: "Attached", "Unattached", "Reserved", etc.
            disk_state = str(disk.disk_state or "").lower()
            state = "unattached" if disk_state == "unattached" else "attached"

            daily = _daily_cost_for_disk(sku or "Standard_LRS", size_gb)
            snapshots.append(
                ResourceSnapshot(
                    resource_id=disk.id or disk.name or "",
                    provider="azure",
                    account_id=self._subscription_id,
                    type="storage",
                    service="ManagedDisk",
                    name=disk.name or "",
                    region=location,
                    daily_cost=daily,
                    monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                    currency="USD",
                    state=state,
                    tags=tags,
                    metadata={
                        "sku": sku,
                        "size_gb": size_gb,
                        "resource_group": rg,
                        "os_type": str(disk.os_type or ""),
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # Load Balancers
    # ------------------------------------------------------------------

    def _collect_load_balancers(self) -> list[ResourceSnapshot]:
        """List all load balancers in the subscription."""
        try:
            from azure.mgmt.network import NetworkManagementClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "azure-mgmt-network is required. "
                "Install it with: pip install azure-mgmt-network"
            ) from exc

        client = NetworkManagementClient(self._credential, self._subscription_id)  # type: ignore[arg-type]
        snapshots: list[ResourceSnapshot] = []

        for lb in client.load_balancers.list_all():
            location = lb.location or "unknown"
            sku_name = lb.sku.name if lb.sku else "Basic"
            tags: dict[str, str] = dict(lb.tags or {})
            rg = _parse_resource_group(lb.id or "")

            frontend_count = len(lb.frontend_ip_configurations or [])
            daily = _daily_cost_for_lb(sku_name)

            snapshots.append(
                ResourceSnapshot(
                    resource_id=lb.id or lb.name or "",
                    provider="azure",
                    account_id=self._subscription_id,
                    type="network",
                    service="LoadBalancer",
                    name=lb.name or "",
                    region=location,
                    daily_cost=daily,
                    monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                    currency="USD",
                    state="active",
                    tags=tags,
                    metadata={
                        "sku": sku_name,
                        "resource_group": rg,
                        "frontend_count": frontend_count,
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # AKS clusters
    # ------------------------------------------------------------------

    def _collect_aks(self) -> list[ResourceSnapshot]:
        """List all AKS clusters and their node pools."""
        try:
            from azure.mgmt.containerservice import (
                ContainerServiceClient,  # type: ignore[import-untyped]
            )
        except ImportError as exc:
            raise ImportError(
                "azure-mgmt-containerservice is required. "
                "Install it with: pip install azure-mgmt-containerservice"
            ) from exc

        client = ContainerServiceClient(self._credential, self._subscription_id)  # type: ignore[arg-type]
        snapshots: list[ResourceSnapshot] = []

        for cluster in client.managed_clusters.list():
            location = cluster.location or "unknown"
            tags: dict[str, str] = dict(cluster.tags or {})
            rg = _parse_resource_group(cluster.id or "")
            state = (cluster.provisioning_state or "unknown").lower()

            # Determine control-plane tier: cluster.sku.tier is "Free", "Standard", or "Premium"
            sku_tier = ""
            if cluster.sku and cluster.sku.tier:
                sku_tier = str(cluster.sku.tier)
            cp_daily = _daily_cost_for_aks_control_plane(sku_tier)

            snapshots.append(
                ResourceSnapshot(
                    resource_id=cluster.id or cluster.name or "",
                    provider="azure",
                    account_id=self._subscription_id,
                    type="kubernetes",
                    service="AKS",
                    name=cluster.name or "",
                    region=location,
                    daily_cost=cp_daily,
                    monthly_cost_estimate=round(cp_daily * _DAYS_PER_MONTH, 4),
                    currency="USD",
                    state=state,
                    tags=tags,
                    metadata={
                        "kubernetes_version": cluster.kubernetes_version or "",
                        "resource_group": rg,
                        "node_resource_group": cluster.node_resource_group or "",
                        "dns_prefix": cluster.dns_prefix or "",
                        "sku_tier": sku_tier,
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

            # Node pools
            for pool in cluster.agent_pool_profiles or []:
                vm_size = pool.vm_size or ""
                node_count = pool.count or 0
                daily_per_node = _daily_cost_for_vm(vm_size, "running")
                daily_total = round(daily_per_node * node_count, 4)
                pool_state = (pool.provisioning_state or "unknown").lower()

                snapshots.append(
                    ResourceSnapshot(
                        resource_id=f"{cluster.id}/agentPools/{pool.name}",
                        provider="azure",
                        account_id=self._subscription_id,
                        type="kubernetes",
                        service="AKS",
                        name=f"{cluster.name}/{pool.name}",
                        region=location,
                        daily_cost=daily_total,
                        monthly_cost_estimate=round(daily_total * _DAYS_PER_MONTH, 4),
                        currency="USD",
                        state=pool_state,
                        tags=tags,
                        metadata={
                            "vm_size": vm_size,
                            "node_count": node_count,
                            "min_count": pool.min_count or 0,
                            "max_count": pool.max_count or 0,
                            "os_disk_size_gb": pool.os_disk_size_gb or 0,
                            "spot": pool.spot_max_price is not None,
                        },
                        snapshot_time=self._snapshot_time,
                    )
                )

        return snapshots

    # ------------------------------------------------------------------
    # Storage Accounts
    # ------------------------------------------------------------------

    def _collect_storage_accounts(self) -> list[ResourceSnapshot]:
        """List all Storage Accounts in the subscription.

        Storage costs depend on data volume which is unavailable without
        monitoring APIs, so daily_cost is set to 0.0. The inventory is
        still useful for waste-detection (unused accounts) and auditing.
        """
        try:
            from azure.mgmt.storage import StorageManagementClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "azure-mgmt-storage is required. "
                "Install it with: pip install azure-mgmt-storage"
            ) from exc

        client = StorageManagementClient(self._credential, self._subscription_id)  # type: ignore[arg-type]
        snapshots: list[ResourceSnapshot] = []

        for account in client.storage_accounts.list():
            location = account.location or "unknown"
            sku_name = account.sku.name if account.sku else "Unknown"
            kind = str(account.kind or "Unknown")
            tags: dict[str, str] = dict(account.tags or {})
            rg = _parse_resource_group(account.id or "")

            snapshots.append(
                ResourceSnapshot(
                    resource_id=account.id or account.name or "",
                    provider="azure",
                    account_id=self._subscription_id,
                    type="storage",
                    service="StorageAccount",
                    name=account.name or "",
                    region=location,
                    daily_cost=0.0,
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state="active",
                    tags=tags,
                    metadata={
                        "sku": sku_name,
                        "kind": kind,
                        "resource_group": rg,
                        "access_tier": str(account.access_tier or ""),
                        "https_only": bool(account.enable_https_traffic_only),
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # App Service Plans
    # ------------------------------------------------------------------

    def _collect_app_service_plans(self) -> list[ResourceSnapshot]:
        """List all App Service Plans in the subscription."""
        try:
            from azure.mgmt.web import WebSiteManagementClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "azure-mgmt-web is required. "
                "Install it with: pip install azure-mgmt-web"
            ) from exc

        client = WebSiteManagementClient(self._credential, self._subscription_id)  # type: ignore[arg-type]
        snapshots: list[ResourceSnapshot] = []

        for plan in client.app_service_plans.list():
            location = plan.location or "unknown"
            sku_name = plan.sku.name if plan.sku else ""
            sku_tier = plan.sku.tier if plan.sku else ""
            tags: dict[str, str] = dict(plan.tags or {})
            rg = _parse_resource_group(plan.id or "")
            worker_count = plan.number_of_sites or 0

            daily = _daily_cost_for_app_service_plan(sku_name)

            snapshots.append(
                ResourceSnapshot(
                    resource_id=plan.id or plan.name or "",
                    provider="azure",
                    account_id=self._subscription_id,
                    type="compute",
                    service="AppServicePlan",
                    name=plan.name or "",
                    region=location,
                    daily_cost=daily,
                    monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                    currency="USD",
                    state="active",
                    tags=tags,
                    metadata={
                        "sku_name": sku_name,
                        "sku_tier": sku_tier,
                        "resource_group": rg,
                        "worker_count": worker_count,
                        "kind": str(plan.kind or ""),
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # SQL Databases
    # ------------------------------------------------------------------

    def _collect_sql_databases(self) -> list[ResourceSnapshot]:
        """List all Azure SQL Databases across all SQL servers."""
        try:
            from azure.mgmt.sql import SqlManagementClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "azure-mgmt-sql is required. "
                "Install it with: pip install azure-mgmt-sql"
            ) from exc

        client = SqlManagementClient(self._credential, self._subscription_id)  # type: ignore[arg-type]
        snapshots: list[ResourceSnapshot] = []

        for server in client.servers.list():
            server_name = server.name or ""
            rg = _parse_resource_group(server.id or "")
            for db in client.databases.list_by_server(rg, server_name):
                if (db.name or "").lower() == "master":
                    continue
                location = db.location or server.location or "unknown"
                sku_name = db.sku.name if db.sku else ""
                sku_tier = db.sku.tier if db.sku else ""
                tags: dict[str, str] = dict(db.tags or {})

                daily = _daily_cost_for_sql_db(sku_name)
                snapshots.append(
                    ResourceSnapshot(
                        resource_id=db.id or db.name or "",
                        provider="azure",
                        account_id=self._subscription_id,
                        type="database",
                        service="AzureSQL",
                        name=f"{server_name}/{db.name}",
                        region=location,
                        daily_cost=daily,
                        monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                        currency="USD",
                        state=str(db.status or "unknown").lower(),
                        tags=tags,
                        metadata={
                            "sku_name": sku_name,
                            "sku_tier": sku_tier,
                            "server_name": server_name,
                            "resource_group": rg,
                            "max_size_bytes": db.max_size_bytes or 0,
                            "elastic_pool_id": db.elastic_pool_id or "",
                        },
                        snapshot_time=self._snapshot_time,
                    )
                )

        return snapshots

    # ------------------------------------------------------------------
    # Function Apps (serverless)
    # ------------------------------------------------------------------

    def _collect_function_apps(self) -> list[ResourceSnapshot]:
        """List all Function Apps in the subscription."""
        try:
            from azure.mgmt.web import WebSiteManagementClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "azure-mgmt-web is required. "
                "Install it with: pip install azure-mgmt-web"
            ) from exc

        client = WebSiteManagementClient(self._credential, self._subscription_id)  # type: ignore[arg-type]
        snapshots: list[ResourceSnapshot] = []

        for app in client.web_apps.list():
            kind = str(app.kind or "").lower()
            if "functionapp" not in kind:
                continue

            location = app.location or "unknown"
            tags: dict[str, str] = dict(app.tags or {})
            rg = _parse_resource_group(app.id or "")
            state = str(app.state or "unknown").lower()

            snapshots.append(
                ResourceSnapshot(
                    resource_id=app.id or app.name or "",
                    provider="azure",
                    account_id=self._subscription_id,
                    type="serverless",
                    service="FunctionApp",
                    name=app.name or "",
                    region=location,
                    daily_cost=0.0,  # consumption plan is pay-per-invocation
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state=state,
                    tags=tags,
                    metadata={
                        "kind": kind,
                        "resource_group": rg,
                        "default_host_name": app.default_host_name or "",
                        "https_only": bool(app.https_only),
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # VPN Gateways
    # ------------------------------------------------------------------

    def _collect_vpn_gateways(self) -> list[ResourceSnapshot]:
        """List all VPN Gateways in the subscription."""
        try:
            from azure.mgmt.network import NetworkManagementClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "azure-mgmt-network is required. "
                "Install it with: pip install azure-mgmt-network"
            ) from exc

        from azure.mgmt.resource import ResourceManagementClient  # type: ignore[import-untyped]

        client = NetworkManagementClient(self._credential, self._subscription_id)  # type: ignore[arg-type]
        rm_client = ResourceManagementClient(self._credential, self._subscription_id)  # type: ignore[arg-type]
        snapshots: list[ResourceSnapshot] = []

        for rg_item in rm_client.resource_groups.list():
            rg_name = rg_item.name or ""
            for gw in client.virtual_network_gateways.list(rg_name):
                location = gw.location or "unknown"
                sku_name = gw.sku.name if gw.sku else ""
                tags: dict[str, str] = dict(gw.tags or {})
                rg = _parse_resource_group(gw.id or "")

                daily = _daily_cost_for_vpn_gateway(sku_name)
                snapshots.append(
                    ResourceSnapshot(
                        resource_id=gw.id or gw.name or "",
                        provider="azure",
                        account_id=self._subscription_id,
                        type="network",
                        service="VPNGateway",
                        name=gw.name or "",
                        region=location,
                        daily_cost=daily,
                        monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                        currency="USD",
                        state="active",
                        tags=tags,
                        metadata={
                            "sku": sku_name,
                            "resource_group": rg,
                            "gateway_type": str(gw.gateway_type or ""),
                            "vpn_type": str(gw.vpn_type or ""),
                        },
                        snapshot_time=self._snapshot_time,
                    )
                )

        return snapshots

    # ------------------------------------------------------------------
    # CDN Profiles
    # ------------------------------------------------------------------

    def _collect_cdn_profiles(self) -> list[ResourceSnapshot]:
        """List all CDN profiles in the subscription."""
        try:
            from azure.mgmt.cdn import CdnManagementClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "azure-mgmt-cdn is required. "
                "Install it with: pip install azure-mgmt-cdn"
            ) from exc

        client = CdnManagementClient(self._credential, self._subscription_id)  # type: ignore[arg-type]
        snapshots: list[ResourceSnapshot] = []

        for profile in client.profiles.list():
            location = profile.location or "global"
            sku_name = profile.sku.name if profile.sku else ""
            tags: dict[str, str] = dict(profile.tags or {})
            rg = _parse_resource_group(profile.id or "")

            snapshots.append(
                ResourceSnapshot(
                    resource_id=profile.id or profile.name or "",
                    provider="azure",
                    account_id=self._subscription_id,
                    type="network",
                    service="CDN",
                    name=profile.name or "",
                    region=location,
                    daily_cost=0.0,  # usage-based
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state="active",
                    tags=tags,
                    metadata={
                        "sku": sku_name,
                        "resource_group": rg,
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # Cosmos DB Accounts
    # ------------------------------------------------------------------

    def _collect_cosmos_db(self) -> list[ResourceSnapshot]:
        """List all Cosmos DB accounts in the subscription."""
        try:
            from azure.mgmt.cosmosdb import CosmosDBManagementClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "azure-mgmt-cosmosdb is required. "
                "Install it with: pip install azure-mgmt-cosmosdb"
            ) from exc

        client = CosmosDBManagementClient(self._credential, self._subscription_id)  # type: ignore[arg-type]
        snapshots: list[ResourceSnapshot] = []

        for account in client.database_accounts.list():
            location = account.location or "unknown"
            tags: dict[str, str] = dict(account.tags or {})
            rg = _parse_resource_group(account.id or "")

            # Cosmos DB cost depends on provisioned RU/s which requires
            # additional API calls; set to 0.0 for inventory purposes.
            snapshots.append(
                ResourceSnapshot(
                    resource_id=account.id or account.name or "",
                    provider="azure",
                    account_id=self._subscription_id,
                    type="database",
                    service="CosmosDB",
                    name=account.name or "",
                    region=location,
                    daily_cost=0.0,
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state="active",
                    tags=tags,
                    metadata={
                        "kind": str(account.kind or ""),
                        "resource_group": rg,
                        "consistency_level": str(
                            account.consistency_policy.default_consistency_level
                            if account.consistency_policy else ""
                        ),
                        "enable_automatic_failover": bool(
                            account.enable_automatic_failover
                        ),
                        "database_account_offer_type": str(
                            account.database_account_offer_type or ""
                        ),
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # CPU Metrics (Azure Monitor)
    # ------------------------------------------------------------------

    def collect_cpu_metrics(self, snapshots: list[ResourceSnapshot]) -> list[ResourceSnapshot]:
        """Enrich running VMs with average CPU utilization from Azure Monitor."""
        try:
            from azure.mgmt.monitor import MonitorManagementClient  # type: ignore[import-untyped]
        except ImportError:
            logger.debug("azure-mgmt-monitor not installed, skipping CPU metrics")
            return snapshots

        try:
            client = MonitorManagementClient(self._credential, self._subscription_id)  # type: ignore[arg-type]
        except Exception:
            logger.warning("Could not create Azure Monitor client", exc_info=True)
            return snapshots

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=IDLE_CPU_LOOKBACK_DAYS)
        timespan = f"{start.isoformat()}/{now.isoformat()}"

        for snap in snapshots:
            if snap.service != "VirtualMachine" or snap.state != "running":
                continue
            try:
                result = client.metrics.list(
                    resource_uri=snap.resource_id,
                    timespan=timespan,
                    interval="P1D",
                    metricnames="Percentage CPU",
                    aggregation="Average",
                )
                values: list[float] = []
                for metric in result.value:
                    for ts in metric.timeseries:
                        for dp in ts.data:
                            if dp.average is not None:
                                values.append(dp.average)
                if values:
                    from cloud.metrics_util import enrich_from_daily_avg_list
                    enrich_from_daily_avg_list(snap, values, start)
            except Exception:
                logger.debug(
                    "Azure Monitor CPU lookup failed for %s", snap.resource_id, exc_info=True,
                )

        return snapshots

    def collect_resource_metrics(self, snapshots: list[ResourceSnapshot]) -> list[ResourceSnapshot]:
        """Enrich non-compute Azure resources with utilisation metrics.

        Covers: AzureSQL (DTU/CPU, connections), LoadBalancer (data path),
        FunctionApp (function execution count).
        """
        try:
            from azure.mgmt.monitor import MonitorManagementClient  # type: ignore[import-untyped]
        except ImportError:
            logger.debug("azure-mgmt-monitor not installed, skipping resource metrics")
            return snapshots

        try:
            client = MonitorManagementClient(self._credential, self._subscription_id)  # type: ignore[arg-type]
        except Exception:
            logger.warning("Could not create Azure Monitor client for resource metrics", exc_info=True)
            return snapshots

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=IDLE_CPU_LOOKBACK_DAYS)
        timespan = f"{start.isoformat()}/{now.isoformat()}"

        _metric_map = {
            "AzureSQL": [
                ("dtu_consumption_percent", "avg_dtu_percent", "Average"),
                ("connection_successful", "avg_connections", "Average"),
            ],
            "CosmosDB": [
                ("TotalRequests", "total_requests", "Count"),
            ],
            "LoadBalancer": [
                ("ByteCount", "total_bytes", "Total"),
            ],
            "FunctionApp": [
                ("FunctionExecutionCount", "total_invocations", "Total"),
            ],
        }

        for snap in snapshots:
            metrics = _metric_map.get(snap.service)
            if not metrics:
                continue
            for metric_name, meta_key, aggregation in metrics:
                try:
                    result = client.metrics.list(
                        resource_uri=snap.resource_id,
                        timespan=timespan,
                        interval="P1D",
                        metricnames=metric_name,
                        aggregation=aggregation,
                    )
                    values: list[float] = []
                    for metric in result.value:
                        for ts in metric.timeseries:
                            for dp in ts.data:
                                val = getattr(dp, aggregation.lower(), None) or dp.average
                                if val is not None:
                                    values.append(val)
                    if values:
                        if "total" in meta_key:
                            snap.metadata[meta_key] = round(sum(values))
                        else:
                            snap.metadata[meta_key] = round(sum(values) / len(values), 2)
                except Exception:
                    logger.debug("Azure metric %s failed for %s", metric_name, snap.resource_id, exc_info=True)

        return snapshots
