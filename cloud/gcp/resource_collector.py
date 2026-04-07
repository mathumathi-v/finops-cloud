# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from cloud.pricing import PricingProvider
from cost_model.models import ResourceSnapshot
from intelligence.constants import IDLE_CPU_LOOKBACK_DAYS

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

# Cloud SQL tier → approximate hourly on-demand USD (us-central1).
_CLOUD_SQL_HOURLY_USD: dict[str, float] = {
    "db-f1-micro": 0.0150, "db-g1-small": 0.0500,
    "db-n1-standard-1": 0.0965, "db-n1-standard-2": 0.1930,
    "db-n1-standard-4": 0.3860, "db-n1-standard-8": 0.7720,
    "db-n1-standard-16": 1.5440,
    "db-n1-highmem-2": 0.2370, "db-n1-highmem-4": 0.4740,
    "db-n1-highmem-8": 0.9480, "db-n1-highmem-16": 1.8960,
    "db-custom-1-3840": 0.0590, "db-custom-2-7680": 0.1180,
    "db-custom-4-15360": 0.2360, "db-custom-8-30720": 0.4720,
}

# Cloud SQL storage per-GB per-month USD.
_CLOUD_SQL_STORAGE_GB_MONTH: dict[str, float] = {
    "pd-ssd": 0.17, "pd-hdd": 0.09,
}

_HOURS_PER_DAY = 24.0
_DAYS_PER_MONTH = 30.0


def _daily_cost_for_instance(
    machine_type: str, status: str, hourly_override: float | None = None,
) -> float:
    """Estimate daily cost for a Compute Engine instance from machine type."""
    if status.lower() != "running":
        return 0.0
    mt = machine_type.rsplit("/", 1)[-1].lower()
    hourly = hourly_override if hourly_override is not None else _MACHINE_HOURLY_USD.get(mt, 0.0)
    return round(hourly * _HOURS_PER_DAY, 6)


def _daily_cost_for_disk(disk_type: str, size_gb: int) -> float:
    """Estimate daily cost for a Persistent Disk from type and size."""
    dt = disk_type.rsplit("/", 1)[-1].lower()
    monthly_per_gb = _PD_PRICE_PER_GB_MONTH.get(dt, 0.04)
    return round(size_gb * monthly_per_gb / _DAYS_PER_MONTH, 6)


def _daily_cost_for_cloud_sql(tier: str, storage_gb: int, storage_type: str) -> float:
    """Estimate daily cost for a Cloud SQL instance from tier and storage."""
    hourly = _CLOUD_SQL_HOURLY_USD.get(tier.lower(), 0.0)
    compute_daily = hourly * _HOURS_PER_DAY
    st_key = "pd-ssd" if "ssd" in storage_type.lower() else "pd-hdd"
    storage_monthly_per_gb = _CLOUD_SQL_STORAGE_GB_MONTH.get(st_key, 0.17)
    storage_daily = storage_gb * storage_monthly_per_gb / _DAYS_PER_MONTH
    return round(compute_daily + storage_daily, 6)


def _zone_to_region(zone: str) -> str:
    """Convert a GCP zone (us-central1-a) to a region (us-central1)."""
    parts = zone.rsplit("/", 1)[-1].rsplit("-", 1)
    return parts[0] if len(parts) == 2 else zone


class GCPResourceCollector:
    """Fetches live GCP resource metadata."""

    def __init__(
        self,
        project_id: str,
        credentials: object | None = None,
        pricing_provider: PricingProvider | None = None,
    ) -> None:
        """
        Args:
            project_id: GCP project ID to collect resources from.
            credentials: Optional google.oauth2 credentials. If None,
                Application Default Credentials are used.
            pricing_provider: Optional dynamic pricing provider.
        """
        self._project_id = project_id
        self._credentials = credentials
        self._pricing = pricing_provider
        self._snapshot_time = datetime.now(timezone.utc)

    def collect_resources(self) -> list[ResourceSnapshot]:
        """Collect all supported GCP resources for the project."""
        self._snapshot_time = datetime.now(timezone.utc)
        snapshots: list[ResourceSnapshot] = []
        collectors = [
            ("Compute VMs", self._collect_instances),
            ("Persistent Disks", self._collect_disks),
            ("Load Balancers", self._collect_load_balancers),
            ("GKE clusters", self._collect_gke),
            ("Cloud SQL", self._collect_cloud_sql),
            ("Cloud Storage", self._collect_gcs),
            ("Cloud Run", self._collect_cloud_run),
            ("Cloud Functions", self._collect_cloud_functions),
            ("BigQuery", self._collect_bigquery),
            ("Pub/Sub", self._collect_pubsub),
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

    # ------------------------------------------------------------------
    # Cloud SQL instances
    # ------------------------------------------------------------------

    def _collect_cloud_sql(self) -> list[ResourceSnapshot]:
        """List all Cloud SQL instances in the project."""
        try:
            from googleapiclient.discovery import build  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "google-api-python-client is required for Cloud SQL collection. "
                "Install it with: pip install google-api-python-client"
            ) from exc

        kwargs: dict[str, Any] = {}
        if self._credentials:
            kwargs["credentials"] = self._credentials
        service = build("sqladmin", "v1", **kwargs, cache_discovery=False)

        snapshots: list[ResourceSnapshot] = []
        resp = service.instances().list(project=self._project_id).execute()

        for inst in resp.get("items", []):
            state = inst.get("state", "UNKNOWN").lower()
            tier = inst.get("settings", {}).get("tier", "")
            storage_gb = int(inst.get("settings", {}).get("dataDiskSizeGb", 0))
            storage_type = inst.get("settings", {}).get("dataDiskType", "PD_SSD")
            region = inst.get("region", "")
            labels: dict[str, str] = dict(inst.get("settings", {}).get("userLabels", {}))

            daily = _daily_cost_for_cloud_sql(tier, storage_gb, storage_type)
            snapshots.append(
                ResourceSnapshot(
                    resource_id=inst.get("selfLink", inst.get("name", "")),
                    provider="gcp",
                    account_id=self._project_id,
                    type="database",
                    service="CloudSQL",
                    name=inst.get("name", ""),
                    region=region,
                    daily_cost=daily,
                    monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                    currency="USD",
                    state=state,
                    tags=labels,
                    metadata={
                        "database_version": inst.get("databaseVersion", ""),
                        "tier": tier,
                        "data_disk_size_gb": storage_gb,
                        "data_disk_type": storage_type,
                        "availability_type": inst.get("settings", {}).get(
                            "availabilityType", ""
                        ),
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # Cloud Storage buckets
    # ------------------------------------------------------------------

    def _collect_gcs(self) -> list[ResourceSnapshot]:
        """List all Cloud Storage buckets in the project."""
        try:
            from google.cloud import storage  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "google-cloud-storage is required for GCS collection. "
                "Install it with: pip install google-cloud-storage"
            ) from exc

        kwargs: dict[str, Any] = {"project": self._project_id}
        if self._credentials:
            kwargs["credentials"] = self._credentials
        client = storage.Client(**kwargs)

        snapshots: list[ResourceSnapshot] = []
        for bucket in client.list_buckets():
            labels: dict[str, str] = dict(bucket.labels or {})
            snapshots.append(
                ResourceSnapshot(
                    resource_id=f"gs://{bucket.name}",
                    provider="gcp",
                    account_id=self._project_id,
                    type="storage",
                    service="GCS",
                    name=bucket.name,
                    region=bucket.location or "unknown",
                    daily_cost=0.0,  # size-dependent; use billing export
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state="active",
                    tags=labels,
                    metadata={
                        "storage_class": bucket.storage_class or "",
                        "location_type": bucket.location_type or "",
                        "versioning_enabled": bool(bucket.versioning_enabled),
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # Cloud Run services
    # ------------------------------------------------------------------

    def _collect_cloud_run(self) -> list[ResourceSnapshot]:
        """List all Cloud Run services in the project."""
        try:
            from google.cloud import run_v2  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "google-cloud-run is required for Cloud Run collection. "
                "Install it with: pip install google-cloud-run"
            ) from exc

        kwargs: dict[str, Any] = {}
        if self._credentials:
            kwargs["credentials"] = self._credentials
        client = run_v2.ServicesClient(**kwargs)
        parent = f"projects/{self._project_id}/locations/-"

        snapshots: list[ResourceSnapshot] = []
        for svc in client.list_services(parent=parent):
            name = svc.name or ""
            # Extract region from name: projects/p/locations/REGION/services/NAME
            parts = name.split("/")
            region = parts[3] if len(parts) >= 5 else "unknown"
            short_name = parts[-1] if parts else name
            labels: dict[str, str] = dict(svc.labels or {})

            snapshots.append(
                ResourceSnapshot(
                    resource_id=name,
                    provider="gcp",
                    account_id=self._project_id,
                    type="serverless",
                    service="CloudRun",
                    name=short_name,
                    region=region,
                    daily_cost=0.0,  # request/CPU based
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state="active",
                    tags=labels,
                    metadata={
                        "uri": svc.uri or "",
                        "launch_stage": str(svc.launch_stage) if svc.launch_stage else "",
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # Cloud Functions
    # ------------------------------------------------------------------

    def _collect_cloud_functions(self) -> list[ResourceSnapshot]:
        """List all Cloud Functions (v2) in the project."""
        try:
            from google.cloud.functions_v2 import FunctionServiceClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "google-cloud-functions is required for Cloud Functions collection. "
                "Install it with: pip install google-cloud-functions"
            ) from exc

        kwargs: dict[str, Any] = {}
        if self._credentials:
            kwargs["credentials"] = self._credentials
        client = FunctionServiceClient(**kwargs)
        parent = f"projects/{self._project_id}/locations/-"

        snapshots: list[ResourceSnapshot] = []
        for fn in client.list_functions(parent=parent):
            name = fn.name or ""
            parts = name.split("/")
            region = parts[3] if len(parts) >= 5 else "unknown"
            short_name = parts[-1] if parts else name
            labels: dict[str, str] = dict(fn.labels or {})

            snapshots.append(
                ResourceSnapshot(
                    resource_id=name,
                    provider="gcp",
                    account_id=self._project_id,
                    type="serverless",
                    service="CloudFunctions",
                    name=short_name,
                    region=region,
                    daily_cost=0.0,  # invocation-based
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state=str(fn.state).lower() if fn.state else "active",
                    tags=labels,
                    metadata={
                        "runtime": fn.build_config.runtime if fn.build_config else "",
                        "entry_point": fn.build_config.entry_point if fn.build_config else "",
                        "environment": str(fn.environment) if fn.environment else "",
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # BigQuery datasets
    # ------------------------------------------------------------------

    def _collect_bigquery(self) -> list[ResourceSnapshot]:
        """List all BigQuery datasets in the project."""
        try:
            from google.cloud import bigquery  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "google-cloud-bigquery is required. "
                "Install it with: pip install google-cloud-bigquery"
            ) from exc

        kwargs: dict[str, Any] = {"project": self._project_id}
        if self._credentials:
            kwargs["credentials"] = self._credentials
        client = bigquery.Client(**kwargs)

        snapshots: list[ResourceSnapshot] = []
        for dataset in client.list_datasets():
            ref = dataset.reference
            ds = client.get_dataset(ref)
            labels: dict[str, str] = dict(ds.labels or {})

            snapshots.append(
                ResourceSnapshot(
                    resource_id=str(ref),
                    provider="gcp",
                    account_id=self._project_id,
                    type="managed_service",
                    service="BigQuery",
                    name=dataset.dataset_id,
                    region=ds.location or "US",
                    daily_cost=0.0,  # query + storage based
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state="active",
                    tags=labels,
                    metadata={
                        "default_table_expiration_ms": ds.default_table_expiration_ms,
                        "description": ds.description or "",
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # Pub/Sub topics
    # ------------------------------------------------------------------

    def _collect_pubsub(self) -> list[ResourceSnapshot]:
        """List all Pub/Sub topics in the project."""
        try:
            from google.cloud import pubsub_v1  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "google-cloud-pubsub is required. "
                "Install it with: pip install google-cloud-pubsub"
            ) from exc

        kwargs: dict[str, Any] = {}
        if self._credentials:
            kwargs["credentials"] = self._credentials
        publisher = pubsub_v1.PublisherClient(**kwargs)
        project_path = f"projects/{self._project_id}"

        snapshots: list[ResourceSnapshot] = []
        for topic in publisher.list_topics(request={"project": project_path}):
            name = topic.name or ""
            short_name = name.rsplit("/", 1)[-1]
            labels: dict[str, str] = dict(topic.labels or {})

            snapshots.append(
                ResourceSnapshot(
                    resource_id=name,
                    provider="gcp",
                    account_id=self._project_id,
                    type="managed_service",
                    service="PubSub",
                    name=short_name,
                    region="global",
                    daily_cost=0.0,  # message-volume based
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state="active",
                    tags=labels,
                    metadata={},
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # CPU Metrics (Cloud Monitoring)
    # ------------------------------------------------------------------

    def collect_cpu_metrics(self, snapshots: list[ResourceSnapshot]) -> list[ResourceSnapshot]:
        """Enrich running GCE instances with average CPU utilization from Cloud Monitoring."""
        try:
            from google.cloud import monitoring_v3  # type: ignore[import-untyped]
            from google.protobuf.timestamp_pb2 import Timestamp  # type: ignore[import-untyped]
        except ImportError:
            logger.debug("google-cloud-monitoring not installed, skipping CPU metrics")
            return snapshots

        kwargs: dict[str, Any] = {}
        if self._credentials:
            kwargs["credentials"] = self._credentials
        try:
            client = monitoring_v3.MetricServiceClient(**kwargs)
        except Exception:
            logger.warning("Could not create Cloud Monitoring client", exc_info=True)
            return snapshots

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=IDLE_CPU_LOOKBACK_DAYS)

        for snap in snapshots:
            if snap.service != "GCE" or snap.state != "running":
                continue
            try:
                # Extract instance ID from self_link
                instance_id = snap.resource_id.rsplit("/", 1)[-1]
                interval = monitoring_v3.TimeInterval(
                    end_time=Timestamp(seconds=int(now.timestamp())),
                    start_time=Timestamp(seconds=int(start.timestamp())),
                )
                results = client.list_time_series(
                    request={
                        "name": f"projects/{self._project_id}",
                        "filter": (
                            'metric.type="compute.googleapis.com/instance/cpu/utilization" '
                            f'AND resource.labels.instance_id="{instance_id}"'
                        ),
                        "interval": interval,
                        "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                    }
                )
                values: list[float] = []
                for ts in results:
                    for point in ts.points:
                        values.append(point.value.double_value * 100)  # fraction → %
                if values:
                    from cloud.metrics_util import enrich_from_daily_avg_list
                    enrich_from_daily_avg_list(snap, list(reversed(values)), start)
            except Exception:
                logger.debug(
                    "Cloud Monitoring CPU lookup failed for %s", snap.resource_id, exc_info=True,
                )

        return snapshots

    def collect_resource_metrics(self, snapshots: list[ResourceSnapshot]) -> list[ResourceSnapshot]:
        """Enrich non-compute GCP resources with utilisation metrics.

        Covers: CloudSQL (connections, CPU), LoadBalancer (request count),
        CloudRun/CloudFunctions (invocations).
        Metrics are fetched from Cloud Monitoring and stored in metadata.
        """
        try:
            from google.cloud import monitoring_v3  # type: ignore[import-untyped]
            from google.protobuf.timestamp_pb2 import Timestamp  # type: ignore[import-untyped]
        except ImportError:
            logger.debug("google-cloud-monitoring not installed, skipping resource metrics")
            return snapshots

        kwargs: dict[str, Any] = {}
        if self._credentials:
            kwargs["credentials"] = self._credentials
        try:
            client = monitoring_v3.MetricServiceClient(**kwargs)
        except Exception:
            logger.warning("Could not create Cloud Monitoring client for resource metrics", exc_info=True)
            return snapshots

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=IDLE_CPU_LOOKBACK_DAYS)
        interval = monitoring_v3.TimeInterval(
            end_time=Timestamp(seconds=int(now.timestamp())),
            start_time=Timestamp(seconds=int(start.timestamp())),
        )

        _metric_map = {
            "CloudSQL": [
                ("cloudsql.googleapis.com/database/network/connections", "avg_connections"),
                ("cloudsql.googleapis.com/database/cpu/utilization", "avg_cpu_percent"),
            ],
            "CloudRun": [
                ("run.googleapis.com/request_count", "total_invocations"),
            ],
            "CloudFunctions": [
                ("cloudfunctions.googleapis.com/function/execution_count", "total_invocations"),
            ],
        }

        for snap in snapshots:
            metrics = _metric_map.get(snap.service)
            if not metrics:
                continue
            for metric_type, meta_key in metrics:
                try:
                    results = client.list_time_series(
                        request={
                            "name": f"projects/{self._project_id}",
                            "filter": f'metric.type="{metric_type}"',
                            "interval": interval,
                            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                        }
                    )
                    values: list[float] = []
                    for ts in results:
                        for point in ts.points:
                            values.append(point.value.double_value)
                    if values:
                        if "total" in meta_key:
                            snap.metadata[meta_key] = round(sum(values))
                        elif "cpu" in meta_key:
                            snap.metadata[meta_key] = round(sum(values) / len(values) * 100, 2)
                        else:
                            snap.metadata[meta_key] = round(sum(values) / len(values), 2)
                except Exception:
                    logger.debug("GCP metric %s failed for %s", metric_type, snap.resource_id, exc_info=True)

        return snapshots
