# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import UTC, datetime

from cost_model.models import ResourceSnapshot

logger = logging.getLogger(__name__)

# Azure VM size → approximate hourly on-demand USD (East US, Linux).
# Used only when billing data is not available per-resource.
_VM_HOURLY_USD: dict[str, float] = {
    "standard_b1s": 0.0104,
    "standard_b1ms": 0.0207,
    "standard_b2s": 0.0416,
    "standard_b2ms": 0.0832,
    "standard_b4ms": 0.166,
    "standard_d2s_v3": 0.096,
    "standard_d4s_v3": 0.192,
    "standard_d8s_v3": 0.384,
    "standard_d2s_v4": 0.096,
    "standard_d4s_v4": 0.192,
    "standard_d2s_v5": 0.096,
    "standard_d4s_v5": 0.192,
    "standard_e2s_v3": 0.126,
    "standard_e4s_v3": 0.252,
    "standard_f2s_v2": 0.085,
    "standard_f4s_v2": 0.169,
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

_HOURS_PER_DAY = 24.0
_DAYS_PER_MONTH = 30.0


def _daily_cost_for_vm(vm_size: str, power_state: str) -> float:
    """Estimate daily cost for a VM from its size and power state."""
    if "deallocated" in power_state.lower() or "stopped" in power_state.lower():
        return 0.0
    hourly = _VM_HOURLY_USD.get(vm_size.lower(), 0.0)
    return round(hourly * _HOURS_PER_DAY, 6)


def _daily_cost_for_disk(sku: str, size_gb: int) -> float:
    """Estimate daily cost for a managed disk from SKU and size."""
    sku_key = sku.lower().replace(" ", "_").replace("-", "_")
    monthly_per_gb = _DISK_PRICE_PER_GB_MONTH.get(sku_key, 0.04)
    return round(size_gb * monthly_per_gb / _DAYS_PER_MONTH, 6)


def _parse_resource_group(resource_id: str) -> str:
    """Extract resource group name from an Azure resource ID."""
    parts = resource_id.lower().split("/")
    try:
        idx = parts.index("resourcegroups")
        return resource_id.split("/")[idx + 1]
    except (ValueError, IndexError):
        return ""


class AzureResourceCollector:
    """Fetches live Azure resource metadata (VMs, Disks, Load Balancers, AKS)."""

    def __init__(self, subscription_id: str, credential: object) -> None:
        """
        Args:
            subscription_id: Azure subscription ID.
            credential: An azure-identity credential object.
        """
        self._subscription_id = subscription_id
        self._credential = credential
        self._snapshot_time = datetime.now(UTC)

    def collect_resources(self) -> list[ResourceSnapshot]:
        """Collect all supported Azure resources for the subscription."""
        self._snapshot_time = datetime.now(UTC)
        snapshots: list[ResourceSnapshot] = []
        collectors = [
            ("VMs", self._collect_vms),
            ("Managed Disks", self._collect_disks),
            ("Load Balancers", self._collect_load_balancers),
            ("AKS clusters", self._collect_aks),
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

            snapshots.append(
                ResourceSnapshot(
                    resource_id=lb.id or lb.name or "",
                    provider="azure",
                    account_id=self._subscription_id,
                    type="network",
                    service="LoadBalancer",
                    name=lb.name or "",
                    region=location,
                    daily_cost=0.0,
                    monthly_cost_estimate=0.0,
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

            snapshots.append(
                ResourceSnapshot(
                    resource_id=cluster.id or cluster.name or "",
                    provider="azure",
                    account_id=self._subscription_id,
                    type="kubernetes",
                    service="AKS",
                    name=cluster.name or "",
                    region=location,
                    daily_cost=0.0,
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state=state,
                    tags=tags,
                    metadata={
                        "kubernetes_version": cluster.kubernetes_version or "",
                        "resource_group": rg,
                        "node_resource_group": cluster.node_resource_group or "",
                        "dns_prefix": cluster.dns_prefix or "",
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
