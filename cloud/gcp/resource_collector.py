# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import UTC, datetime
from typing import Any

from cost_model.models import ResourceSnapshot

logger = logging.getLogger(__name__)

# GCP machine-type → approximate hourly on-demand USD (us-central1, spot ignored).
# These are rough estimates used to populate daily_cost when billing data is not
# available per-resource.  The billing export in BigQuery is the source of truth.
_MACHINE_HOURLY_USD: dict[str, float] = {
    "e2-micro": 0.0084,
    "e2-small": 0.0168,
    "e2-medium": 0.0335,
    "e2-standard-2": 0.0671,
    "e2-standard-4": 0.1342,
    "e2-standard-8": 0.2684,
    "e2-standard-16": 0.5368,
    "e2-standard-32": 1.0736,
    "n1-standard-1": 0.0475,
    "n1-standard-2": 0.095,
    "n1-standard-4": 0.19,
    "n1-standard-8": 0.38,
    "n2-standard-2": 0.0971,
    "n2-standard-4": 0.1942,
    "n2-standard-8": 0.3885,
    "c2-standard-4": 0.2088,
    "c2-standard-8": 0.4176,
}

_PD_PRICE_PER_GB_MONTH: dict[str, float] = {
    "pd-standard": 0.04,
    "pd-balanced": 0.10,
    "pd-ssd": 0.17,
    "pd-extreme": 0.12,
    "hyperdisk-balanced": 0.12,
}

_HOURS_PER_DAY = 24.0
_DAYS_PER_MONTH = 30.0


def _daily_cost_for_instance(machine_type: str, status: str) -> float:
    """Estimate daily cost for a Compute Engine instance from machine type."""
    if status.lower() != "running":
        return 0.0
    # Strip zone prefix if present (e.g. "zones/us-central1-a/machineTypes/n1-standard-1")
    mt = machine_type.rsplit("/", 1)[-1].lower()
    hourly = _MACHINE_HOURLY_USD.get(mt, 0.0)
    return round(hourly * _HOURS_PER_DAY, 6)


def _daily_cost_for_disk(disk_type: str, size_gb: int) -> float:
    """Estimate daily cost for a Persistent Disk from type and size."""
    dt = disk_type.rsplit("/", 1)[-1].lower()
    monthly_per_gb = _PD_PRICE_PER_GB_MONTH.get(dt, 0.04)
    return round(size_gb * monthly_per_gb / _DAYS_PER_MONTH, 6)


def _zone_to_region(zone: str) -> str:
    """Convert a GCP zone (us-central1-a) to a region (us-central1)."""
    parts = zone.rsplit("/", 1)[-1].rsplit("-", 1)
    return parts[0] if len(parts) == 2 else zone


class GCPResourceCollector:
    """Fetches live GCP resource metadata (Compute VMs, Disks, LBs, GKE)."""

    def __init__(
        self,
        project_id: str,
        credentials: object | None = None,
    ) -> None:
        """
        Args:
            project_id: GCP project ID to collect resources from.
            credentials: Optional google.oauth2 credentials. If None,
                Application Default Credentials are used.
        """
        self._project_id = project_id
        self._credentials = credentials
        self._snapshot_time = datetime.now(UTC)

    def collect_resources(self) -> list[ResourceSnapshot]:
        """Collect all supported GCP resources for the project."""
        self._snapshot_time = datetime.now(UTC)
        snapshots: list[ResourceSnapshot] = []
        collectors = [
            ("Compute VMs", self._collect_instances),
            ("Persistent Disks", self._collect_disks),
            ("Load Balancers", self._collect_load_balancers),
            ("GKE clusters", self._collect_gke),
        ]
        for name, fn in collectors:
            try:
                results = fn()
                snapshots.extend(results)
                logger.info("Collected %d %s resources", len(results), name)
            except Exception:
                logger.warning(
                    "Failed to collect %s (missing permission or API disabled?)",
                    name,
                    exc_info=True,
                )
        logger.info(
            "Collected %d total GCP resources for %s", len(snapshots), self._project_id
        )
        return snapshots

    # ------------------------------------------------------------------
    # Compute Engine instances
    # ------------------------------------------------------------------

    def _compute_client(self) -> Any:
        try:
            from google.cloud import compute_v1  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "google-cloud-compute is required for GCP resource collection. "
                "Install it with: pip install google-cloud-compute"
            ) from exc
        kwargs: dict[str, Any] = {}
        if self._credentials:
            kwargs["credentials"] = self._credentials
        return compute_v1.InstancesClient(**kwargs)

    def _collect_instances(self) -> list[ResourceSnapshot]:
        """List all Compute Engine VMs via aggregated list."""
        from google.cloud import compute_v1  # type: ignore[import-untyped]

        client = self._compute_client()
        request = compute_v1.AggregatedListInstancesRequest(project=self._project_id)

        snapshots: list[ResourceSnapshot] = []
        for _zone, instances_scoped in client.aggregated_list(request=request):
            for inst in instances_scoped.instances or []:
                status = inst.status or "UNKNOWN"
                machine_type = inst.machine_type or ""
                zone = inst.zone or ""
                region = _zone_to_region(zone)
                labels: dict[str, str] = dict(inst.labels or {})

                daily = _daily_cost_for_instance(machine_type, status)
                snapshots.append(
                    ResourceSnapshot(
                        resource_id=str(inst.self_link),
                        provider="gcp",
                        account_id=self._project_id,
                        type="compute",
                        service="GCE",
                        name=inst.name or "",
                        region=region,
                        daily_cost=daily,
                        monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                        currency="USD",
                        state=status.lower(),
                        tags=labels,
                        metadata={
                            "machine_type": machine_type.rsplit("/", 1)[-1],
                            "zone": zone.rsplit("/", 1)[-1],
                            "network": (
                                inst.network_interfaces[0].network.rsplit("/", 1)[-1]
                                if inst.network_interfaces
                                else ""
                            ),
                        },
                        snapshot_time=self._snapshot_time,
                    )
                )
        return snapshots

    # ------------------------------------------------------------------
    # Persistent Disks
    # ------------------------------------------------------------------

    def _collect_disks(self) -> list[ResourceSnapshot]:
        """List all Persistent Disks via aggregated list."""
        from google.cloud import compute_v1  # type: ignore[import-untyped]

        kwargs: dict[str, Any] = {}
        if self._credentials:
            kwargs["credentials"] = self._credentials
        disk_client = compute_v1.DisksClient(**kwargs)
        request = compute_v1.AggregatedListDisksRequest(project=self._project_id)

        snapshots: list[ResourceSnapshot] = []
        for _zone, disks_scoped in disk_client.aggregated_list(request=request):
            for disk in disks_scoped.disks or []:
                users = list(disk.users or [])
                state = "attached" if users else "unattached"
                disk_type = disk.type_ or "pd-standard"
                size_gb = int(disk.size_gb or 0)
                zone = disk.zone or ""
                region = _zone_to_region(zone)
                labels: dict[str, str] = dict(disk.labels or {})

                daily = _daily_cost_for_disk(disk_type, size_gb)
                snapshots.append(
                    ResourceSnapshot(
                        resource_id=str(disk.self_link),
                        provider="gcp",
                        account_id=self._project_id,
                        type="storage",
                        service="PersistentDisk",
                        name=disk.name or "",
                        region=region,
                        daily_cost=daily,
                        monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                        currency="USD",
                        state=state,
                        tags=labels,
                        metadata={
                            "disk_type": disk_type.rsplit("/", 1)[-1],
                            "size_gb": size_gb,
                            "zone": zone.rsplit("/", 1)[-1],
                            "attached_to": users[0].rsplit("/", 1)[-1] if users else "",
                        },
                        snapshot_time=self._snapshot_time,
                    )
                )
        return snapshots

    # ------------------------------------------------------------------
    # Load Balancers (Forwarding Rules as proxy for LB cost)
    # ------------------------------------------------------------------

    def _collect_load_balancers(self) -> list[ResourceSnapshot]:
        """List all global and regional forwarding rules."""
        from google.cloud import compute_v1  # type: ignore[import-untyped]

        kwargs: dict[str, Any] = {}
        if self._credentials:
            kwargs["credentials"] = self._credentials

        snapshots: list[ResourceSnapshot] = []

        # Global forwarding rules (HTTPS LBs, etc.)
        global_client = compute_v1.GlobalForwardingRulesClient(**kwargs)
        global_req = compute_v1.ListGlobalForwardingRulesRequest(project=self._project_id)
        for rule in global_client.list(request=global_req):
            snapshots.append(self._forwarding_rule_snapshot(rule, region="global"))

        # Regional forwarding rules (internal LBs, regional TCP/UDP)
        regional_client = compute_v1.ForwardingRulesClient(**kwargs)
        agg_req = compute_v1.AggregatedListForwardingRulesRequest(project=self._project_id)
        for _region, scoped in regional_client.aggregated_list(request=agg_req):
            for rule in scoped.forwarding_rules or []:
                region = (rule.region or "").rsplit("/", 1)[-1] or "global"
                snapshots.append(self._forwarding_rule_snapshot(rule, region=region))

        return snapshots

    def _forwarding_rule_snapshot(self, rule: Any, region: str) -> ResourceSnapshot:
        """Convert a forwarding rule proto to a ResourceSnapshot."""
        labels: dict[str, str] = dict(rule.labels or {})
        # Forwarding rules have no direct hourly price; mark daily_cost as 0
        # (actual cost comes from the billing export collector).
        return ResourceSnapshot(
            resource_id=str(rule.self_link),
            provider="gcp",
            account_id=self._project_id,
            type="network",
            service="LoadBalancer",
            name=rule.name or "",
            region=region,
            daily_cost=0.0,
            monthly_cost_estimate=0.0,
            currency="USD",
            state="active",
            tags=labels,
            metadata={
                "load_balancing_scheme": rule.load_balancing_scheme or "",
                "ip_protocol": (
                    getattr(rule, "I_p_protocol", None)
                    or getattr(rule, "ip_protocol", "")
                    or ""
                ),
                "port_range": rule.port_range or "",
                "target": (rule.target or "").rsplit("/", 1)[-1],
            },
            snapshot_time=self._snapshot_time,
        )

    # ------------------------------------------------------------------
    # GKE clusters and nodepools
    # ------------------------------------------------------------------

    def _collect_gke(self) -> list[ResourceSnapshot]:
        """List all GKE clusters and their node pools."""
        try:
            from google.cloud import container_v1  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "google-cloud-container is required for GKE collection. "
                "Install it with: pip install google-cloud-container"
            ) from exc

        kwargs: dict[str, Any] = {}
        if self._credentials:
            kwargs["credentials"] = self._credentials
        client = container_v1.ClusterManagerClient(**kwargs)

        # "-" means all locations (zones + regions)
        parent = f"projects/{self._project_id}/locations/-"
        response = client.list_clusters(parent=parent)

        snapshots: list[ResourceSnapshot] = []
        for cluster in response.clusters:
            location = cluster.location or "unknown"
            # Determine if location is a zone or region
            region = location if location.count("-") == 1 else _zone_to_region(location)
            labels: dict[str, str] = dict(cluster.resource_labels or {})

            snapshots.append(
                ResourceSnapshot(
                    resource_id=cluster.self_link or cluster.name,
                    provider="gcp",
                    account_id=self._project_id,
                    type="kubernetes",
                    service="GKE",
                    name=cluster.name,
                    region=region,
                    daily_cost=0.0,
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state=cluster.status.name.lower() if cluster.status else "unknown",
                    tags=labels,
                    metadata={
                        "version": cluster.current_master_version or "",
                        "location": location,
                        "node_count": cluster.current_node_count,
                        "endpoint": cluster.endpoint or "",
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

            for pool in cluster.node_pools:
                config = pool.config
                machine_type = config.machine_type if config else ""
                node_count = pool.initial_node_count or 0

                daily_per_node = _daily_cost_for_instance(machine_type, "running")
                daily_total = round(daily_per_node * node_count, 4)

                snapshots.append(
                    ResourceSnapshot(
                        resource_id=pool.self_link or f"{cluster.name}/{pool.name}",
                        provider="gcp",
                        account_id=self._project_id,
                        type="kubernetes",
                        service="GKE",
                        name=f"{cluster.name}/{pool.name}",
                        region=region,
                        daily_cost=daily_total,
                        monthly_cost_estimate=round(daily_total * _DAYS_PER_MONTH, 4),
                        currency="USD",
                        state=pool.status.name.lower() if pool.status else "unknown",
                        tags=labels,
                        metadata={
                            "machine_type": machine_type,
                            "node_count": node_count,
                            "disk_size_gb": config.disk_size_gb if config else 0,
                            "disk_type": config.disk_type if config else "",
                            "preemptible": config.preemptible if config else False,
                            "spot": config.spot if config else False,
                        },
                        snapshot_time=self._snapshot_time,
                    )
                )

        return snapshots
