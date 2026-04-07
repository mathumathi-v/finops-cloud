# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import datetime, timedelta, timezone

from cost_model.models import ResourceSnapshot
from intelligence.constants import IDLE_CPU_LOOKBACK_DAYS

logger = logging.getLogger(__name__)

# OCI compute shape → approximate hourly on-demand USD (us-ashburn-1).
# Used only when billing data is not available per-resource.
_SHAPE_HOURLY_USD: dict[str, float] = {
    # Flexible (AMD) — per OCPU
    "vm.standard.e4.flex": 0.025,
    "vm.standard.e3.flex": 0.025,
    "vm.standard.e5.flex": 0.026,
    # Flexible (Arm / Ampere A1) — per OCPU
    "vm.standard.a1.flex": 0.01,
    "vm.standard.a2.flex": 0.01,
    # Fixed shapes
    "vm.standard2.1": 0.0638,
    "vm.standard2.2": 0.1276,
    "vm.standard2.4": 0.2552,
    "vm.standard2.8": 0.5104,
    "vm.standard2.16": 1.0208,
    "vm.standard2.24": 1.5312,
    "vm.standard3.flex": 0.054,
    "vm.optimized3.flex": 0.054,
    # Dense I/O
    "vm.denseio2.8": 0.5104,
    "vm.denseio2.16": 1.0208,
    "vm.denseio2.24": 1.5312,
    # GPU
    "vm.gpu2.1": 2.95,
    "vm.gpu3.1": 2.95,
    "vm.gpu3.2": 5.90,
    "vm.gpu3.4": 11.80,
    "bm.gpu4.8": 27.20,
    "bm.gpu.a100-v2.8": 27.20,
    # Bare-metal
    "bm.standard2.52": 3.3124,
    "bm.standard.e4.128": 3.20,
    "bm.standard.e5.192": 4.992,
    "bm.standard.a1.160": 1.60,
}

# Block volume per-GB per-month USD (balanced performance).
_BLOCK_VOLUME_GB_MONTH_USD: dict[str, float] = {
    "balanced": 0.0255,
    "higher_performance": 0.0340,
    "lower_cost": 0.0170,
    "ultra_high_performance": 0.0510,
}

# Load Balancer hourly USD.
_LB_HOURLY_USD: dict[str, float] = {
    "100mbps": 0.01,
    "400mbps": 0.04,
    "8000mbps": 0.22,
    "flexible": 0.014,
}

# OKE cluster management fee — OKE basic clusters are free;
# enhanced clusters cost $0.10/hr per cluster.
_OKE_HOURLY_USD: dict[str, float] = {
    "basic": 0.0,
    "enhanced": 0.10,
}

# Autonomous DB OCPU hourly USD (us-ashburn-1).
_ADB_OCPU_HOURLY_USD: dict[str, float] = {
    "license_included": 3.3606,
    "bring_your_own_license": 1.3441,
}

# Autonomous DB storage per-TB per-month USD.
_ADB_STORAGE_TB_MONTH_USD = 118.40

_HOURS_PER_DAY = 24.0
_DAYS_PER_MONTH = 30.0


def _daily_cost_for_instance(shape: str, ocpus: float, state: str) -> float:
    """Estimate daily cost for a compute instance from shape and OCPU count."""
    if state in ("stopped", "terminated"):
        return 0.0
    hourly = _SHAPE_HOURLY_USD.get(shape.lower(), 0.0)
    # Flex shapes are priced per OCPU
    if "flex" in shape.lower():
        hourly = hourly * ocpus
    return round(hourly * _HOURS_PER_DAY, 6)


def _daily_cost_for_block_volume(size_gb: int, vpus_per_gb: int = 10) -> float:
    """Estimate daily cost for a block volume from size and performance tier."""
    if vpus_per_gb <= 0:
        tier = "lower_cost"
    elif vpus_per_gb <= 10:
        tier = "balanced"
    elif vpus_per_gb <= 20:
        tier = "higher_performance"
    else:
        tier = "ultra_high_performance"
    monthly_per_gb = _BLOCK_VOLUME_GB_MONTH_USD.get(tier, 0.0255)
    return round(size_gb * monthly_per_gb / _DAYS_PER_MONTH, 6)


def _daily_cost_for_lb(bandwidth_mbps: int) -> float:
    """Estimate daily base cost for a load balancer from bandwidth shape."""
    if bandwidth_mbps <= 0:
        key = "flexible"
    elif bandwidth_mbps <= 100:
        key = "100mbps"
    elif bandwidth_mbps <= 400:
        key = "400mbps"
    elif bandwidth_mbps >= 8000:
        key = "8000mbps"
    else:
        key = "flexible"
    hourly = _LB_HOURLY_USD.get(key, 0.014)
    return round(hourly * _HOURS_PER_DAY, 4)


def _daily_cost_for_oke(cluster_type: str) -> float:
    """Estimate daily control-plane cost for an OKE cluster."""
    hourly = _OKE_HOURLY_USD.get(cluster_type.lower(), 0.0)
    return round(hourly * _HOURS_PER_DAY, 4)


def _daily_cost_for_autonomous_db(
    ocpus: float, storage_tbs: float, license_model: str, is_free_tier: bool,
) -> float:
    """Estimate daily cost for an Autonomous Database."""
    if is_free_tier:
        return 0.0
    key = "bring_your_own_license" if "byol" in license_model.lower() else "license_included"
    hourly = _ADB_OCPU_HOURLY_USD.get(key, 1.3441)
    compute_daily = hourly * ocpus * _HOURS_PER_DAY
    storage_daily = storage_tbs * _ADB_STORAGE_TB_MONTH_USD / _DAYS_PER_MONTH
    return round(compute_daily + storage_daily, 6)


class OCIResourceCollector:
    """Fetches live OCI resource metadata."""

    def __init__(self, compartment_id: str, config: object) -> None:
        """
        Args:
            compartment_id: OCI compartment OCID (usually the tenancy root).
            config: An oci.config dict or compatible config object.
        """
        self._compartment_id = compartment_id
        self._config = config
        self._snapshot_time = datetime.now(timezone.utc)

    def collect_resources(self) -> list[ResourceSnapshot]:
        """Collect all supported OCI resources for the compartment."""
        self._snapshot_time = datetime.now(timezone.utc)
        snapshots: list[ResourceSnapshot] = []
        collectors = [
            ("Compute instances", self._collect_instances),
            ("Block Volumes", self._collect_block_volumes),
            ("Load Balancers", self._collect_load_balancers),
            ("OKE clusters", self._collect_oke),
            ("Autonomous Databases", self._collect_autonomous_db),
            ("Object Storage", self._collect_object_storage),
        ]
        for name, fn in collectors:
            try:
                results = fn()
                snapshots.extend(results)
                logger.info("Collected %d OCI %s", len(results), name)
            except Exception:
                logger.warning(
                    "Failed to collect OCI %s (missing permission or service not enabled?)",
                    name,
                    exc_info=True,
                )
        logger.info(
            "Collected %d total OCI resources for compartment %s",
            len(snapshots),
            self._compartment_id,
        )
        return snapshots

    # ------------------------------------------------------------------
    # Compute Instances
    # ------------------------------------------------------------------

    def _collect_instances(self) -> list[ResourceSnapshot]:
        """List all compute instances in the compartment."""
        try:
            import oci  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "oci SDK is required. Install it with: pip install oci"
            ) from exc

        client = oci.core.ComputeClient(self._config)
        snapshots: list[ResourceSnapshot] = []

        instances = oci.pagination.list_call_get_all_results(
            client.list_instances,
            compartment_id=self._compartment_id,
        ).data

        for inst in instances:
            if inst.lifecycle_state == "TERMINATED":
                continue

            shape = inst.shape or ""
            ocpus = 0.0
            memory_gb = 0.0
            if inst.shape_config:
                ocpus = float(inst.shape_config.ocpus or 0)
                memory_gb = float(inst.shape_config.memory_in_gbs or 0)

            state = "running" if inst.lifecycle_state == "RUNNING" else "stopped"

            daily = _daily_cost_for_instance(shape, ocpus, state)
            tags: dict[str, str] = dict(inst.freeform_tags or {})

            snapshots.append(
                ResourceSnapshot(
                    resource_id=inst.id,
                    provider="oci",
                    account_id=self._compartment_id,
                    type="compute",
                    service="Compute",
                    name=inst.display_name or "",
                    region=inst.region or "",
                    daily_cost=daily,
                    monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                    currency="USD",
                    state=state,
                    tags=tags,
                    metadata={
                        "shape": shape,
                        "ocpus": ocpus,
                        "memory_gb": memory_gb,
                        "availability_domain": inst.availability_domain or "",
                        "fault_domain": inst.fault_domain or "",
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # Block Volumes
    # ------------------------------------------------------------------

    def _collect_block_volumes(self) -> list[ResourceSnapshot]:
        """List all block volumes in the compartment."""
        try:
            import oci  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "oci SDK is required. Install it with: pip install oci"
            ) from exc

        client = oci.core.BlockstorageClient(self._config)
        snapshots: list[ResourceSnapshot] = []

        volumes = oci.pagination.list_call_get_all_results(
            client.list_volumes,
            compartment_id=self._compartment_id,
        ).data

        # Get volume attachments to determine attached/unattached
        compute_client = oci.core.ComputeClient(self._config)
        attachments = oci.pagination.list_call_get_all_results(
            compute_client.list_volume_attachments,
            compartment_id=self._compartment_id,
        ).data
        attached_volume_ids = {
            a.volume_id
            for a in attachments
            if a.lifecycle_state == "ATTACHED"
        }

        for vol in volumes:
            if vol.lifecycle_state == "TERMINATED":
                continue

            size_gb = vol.size_in_gbs or 0
            vpus = vol.vpus_per_gb or 10
            tags: dict[str, str] = dict(vol.freeform_tags or {})

            is_attached = vol.id in attached_volume_ids
            state = "attached" if is_attached else "unattached"

            daily = _daily_cost_for_block_volume(size_gb, vpus)

            snapshots.append(
                ResourceSnapshot(
                    resource_id=vol.id,
                    provider="oci",
                    account_id=self._compartment_id,
                    type="storage",
                    service="BlockVolume",
                    name=vol.display_name or "",
                    region="",  # block volume API doesn't expose region directly
                    daily_cost=daily,
                    monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                    currency="USD",
                    state=state,
                    tags=tags,
                    metadata={
                        "size_gb": size_gb,
                        "vpus_per_gb": vpus,
                        "availability_domain": vol.availability_domain or "",
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # Load Balancers
    # ------------------------------------------------------------------

    def _collect_load_balancers(self) -> list[ResourceSnapshot]:
        """List all load balancers in the compartment."""
        try:
            import oci  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "oci SDK is required. Install it with: pip install oci"
            ) from exc

        client = oci.load_balancer.LoadBalancerClient(self._config)
        snapshots: list[ResourceSnapshot] = []

        lbs = oci.pagination.list_call_get_all_results(
            client.list_load_balancers,
            compartment_id=self._compartment_id,
        ).data

        for lb in lbs:
            if lb.lifecycle_state != "ACTIVE":
                continue

            shape = lb.shape_name or "flexible"
            tags: dict[str, str] = dict(lb.freeform_tags or {})

            # Parse bandwidth from shape name (e.g. "100Mbps", "400Mbps", "flexible")
            bandwidth_mbps = 100
            if "flexible" in shape.lower():
                bandwidth_mbps = 0  # will map to "flexible"
            else:
                try:
                    bandwidth_mbps = int("".join(c for c in shape if c.isdigit()) or "100")
                except ValueError:
                    bandwidth_mbps = 100

            daily = _daily_cost_for_lb(bandwidth_mbps)

            snapshots.append(
                ResourceSnapshot(
                    resource_id=lb.id,
                    provider="oci",
                    account_id=self._compartment_id,
                    type="network",
                    service="LoadBalancer",
                    name=lb.display_name or "",
                    region="",
                    daily_cost=daily,
                    monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                    currency="USD",
                    state="active",
                    tags=tags,
                    metadata={
                        "shape": shape,
                        "bandwidth_mbps": bandwidth_mbps,
                        "backend_set_count": len(lb.backend_sets or {}),
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # OKE (Container Engine for Kubernetes)
    # ------------------------------------------------------------------

    def _collect_oke(self) -> list[ResourceSnapshot]:
        """List all OKE clusters and their node pools."""
        try:
            import oci  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "oci SDK is required. Install it with: pip install oci"
            ) from exc

        ce_client = oci.container_engine.ContainerEngineClient(self._config)
        snapshots: list[ResourceSnapshot] = []

        clusters = oci.pagination.list_call_get_all_results(
            ce_client.list_clusters,
            compartment_id=self._compartment_id,
        ).data

        for cluster in clusters:
            if cluster.lifecycle_state in ("DELETED", "DELETING"):
                continue

            state = cluster.lifecycle_state.lower() if cluster.lifecycle_state else "unknown"
            tags: dict[str, str] = dict(cluster.freeform_tags or {})

            # Determine cluster type for pricing
            cluster_type = "basic"
            if cluster.cluster_pod_network_options:
                cluster_type = "enhanced"

            cp_daily = _daily_cost_for_oke(cluster_type)

            snapshots.append(
                ResourceSnapshot(
                    resource_id=cluster.id,
                    provider="oci",
                    account_id=self._compartment_id,
                    type="kubernetes",
                    service="OKE",
                    name=cluster.name or "",
                    region="",
                    daily_cost=cp_daily,
                    monthly_cost_estimate=round(cp_daily * _DAYS_PER_MONTH, 4),
                    currency="USD",
                    state=state,
                    tags=tags,
                    metadata={
                        "kubernetes_version": cluster.kubernetes_version or "",
                        "cluster_type": cluster_type,
                        "vcn_id": cluster.vcn_id or "",
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

            # Node pools
            node_pools = oci.pagination.list_call_get_all_results(
                ce_client.list_node_pools,
                compartment_id=self._compartment_id,
                cluster_id=cluster.id,
            ).data

            for pool in node_pools:
                if pool.lifecycle_state in ("DELETED", "DELETING"):
                    continue

                shape = pool.node_shape or ""
                node_count = pool.node_config_details.size if pool.node_config_details else 0
                ocpus = 0.0
                if pool.node_shape_config:
                    ocpus = float(pool.node_shape_config.ocpus or 0)

                daily_per_node = _daily_cost_for_instance(shape, ocpus, "running")
                daily_total = round(daily_per_node * node_count, 4)
                pool_state = pool.lifecycle_state.lower() if pool.lifecycle_state else "unknown"

                snapshots.append(
                    ResourceSnapshot(
                        resource_id=pool.id,
                        provider="oci",
                        account_id=self._compartment_id,
                        type="kubernetes",
                        service="OKE",
                        name=f"{cluster.name}/{pool.name}",
                        region="",
                        daily_cost=daily_total,
                        monthly_cost_estimate=round(daily_total * _DAYS_PER_MONTH, 4),
                        currency="USD",
                        state=pool_state,
                        tags=tags,
                        metadata={
                            "shape": shape,
                            "ocpus": ocpus,
                            "node_count": node_count,
                            "kubernetes_version": pool.kubernetes_version or "",
                        },
                        snapshot_time=self._snapshot_time,
                    )
                )

        return snapshots

    # ------------------------------------------------------------------
    # Autonomous Databases
    # ------------------------------------------------------------------

    def _collect_autonomous_db(self) -> list[ResourceSnapshot]:
        """List all Autonomous Databases in the compartment."""
        try:
            import oci  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "oci SDK is required. Install it with: pip install oci"
            ) from exc

        client = oci.database.DatabaseClient(self._config)
        snapshots: list[ResourceSnapshot] = []

        adbs = oci.pagination.list_call_get_all_results(
            client.list_autonomous_databases,
            compartment_id=self._compartment_id,
        ).data

        for adb in adbs:
            if adb.lifecycle_state == "TERMINATED":
                continue

            ocpus = float(adb.cpu_core_count or 0)
            storage_tbs = float(adb.data_storage_size_in_tbs or 0)
            is_free = bool(adb.is_free_tier)
            license_model = adb.license_model or "LICENSE_INCLUDED"
            state = adb.lifecycle_state.lower() if adb.lifecycle_state else "unknown"
            tags: dict[str, str] = dict(adb.freeform_tags or {})

            daily = _daily_cost_for_autonomous_db(
                ocpus, storage_tbs, license_model, is_free,
            ) if state == "available" else 0.0

            snapshots.append(
                ResourceSnapshot(
                    resource_id=adb.id,
                    provider="oci",
                    account_id=self._compartment_id,
                    type="database",
                    service="AutonomousDB",
                    name=adb.display_name or "",
                    region="",
                    daily_cost=daily,
                    monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                    currency="USD",
                    state=state,
                    tags=tags,
                    metadata={
                        "db_workload": adb.db_workload or "",
                        "cpu_core_count": ocpus,
                        "data_storage_size_in_tbs": storage_tbs,
                        "is_free_tier": is_free,
                        "db_version": adb.db_version or "",
                        "license_model": license_model,
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # Object Storage
    # ------------------------------------------------------------------

    def _collect_object_storage(self) -> list[ResourceSnapshot]:
        """List all Object Storage buckets in the compartment."""
        try:
            import oci  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "oci SDK is required. Install it with: pip install oci"
            ) from exc

        client = oci.object_storage.ObjectStorageClient(self._config)
        namespace = client.get_namespace().data
        snapshots: list[ResourceSnapshot] = []

        buckets = oci.pagination.list_call_get_all_results(
            client.list_buckets,
            namespace_name=namespace,
            compartment_id=self._compartment_id,
        ).data

        for bucket in buckets:
            tags: dict[str, str] = dict(bucket.freeform_tags or {})
            snapshots.append(
                ResourceSnapshot(
                    resource_id=f"{namespace}/{bucket.name}",
                    provider="oci",
                    account_id=self._compartment_id,
                    type="storage",
                    service="ObjectStorage",
                    name=bucket.name or "",
                    region="",
                    daily_cost=0.0,  # size-dependent; use Usage API
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state="active",
                    tags=tags,
                    metadata={
                        "namespace": namespace,
                        "storage_tier": bucket.storage_tier or "Standard",
                        "time_created": str(bucket.time_created or ""),
                    },
                    snapshot_time=self._snapshot_time,
                )
            )

        return snapshots

    # ------------------------------------------------------------------
    # CPU Metrics (OCI Monitoring)
    # ------------------------------------------------------------------

    def collect_cpu_metrics(self, snapshots: list[ResourceSnapshot]) -> list[ResourceSnapshot]:
        """Enrich running instances with average CPU utilization from OCI Monitoring."""
        try:
            import oci  # type: ignore[import-untyped]
        except ImportError:
            logger.debug("oci SDK not available, skipping CPU metrics")
            return snapshots

        try:
            monitoring = oci.monitoring.MonitoringClient(self._config)
        except Exception:
            logger.warning("Could not create OCI Monitoring client", exc_info=True)
            return snapshots

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=IDLE_CPU_LOOKBACK_DAYS)

        for snap in snapshots:
            if snap.service != "Compute" or snap.state != "running":
                continue
            try:
                resp = monitoring.summarize_metrics_data(
                    compartment_id=self._compartment_id,
                    summarize_metrics_data_details=oci.monitoring.models.SummarizeMetricsDataDetails(
                        namespace="oci_computeagent",
                        query=f'CpuUtilization[1d]{{resourceId = "{snap.resource_id}"}}.mean()',
                        start_time=start.isoformat(),
                        end_time=now.isoformat(),
                    ),
                )
                values: list[float] = []
                for ts in resp.data:
                    for dp in ts.aggregated_datapoints:
                        if dp.value is not None:
                            values.append(float(dp.value))
                if values:
                    from cloud.metrics_util import enrich_from_daily_avg_list
                    enrich_from_daily_avg_list(snap, values, start)
            except Exception:
                logger.debug(
                    "OCI Monitoring CPU lookup failed for %s", snap.resource_id, exc_info=True,
                )

        return snapshots

    def collect_resource_metrics(self, snapshots: list[ResourceSnapshot]) -> list[ResourceSnapshot]:
        """Enrich non-compute OCI resources with utilisation metrics.

        Covers: AutonomousDB (CPU, sessions), LoadBalancer (request count).
        """
        try:
            import oci  # type: ignore[import-untyped]
        except ImportError:
            logger.debug("oci SDK not available, skipping resource metrics")
            return snapshots

        try:
            monitoring = oci.monitoring.MonitoringClient(self._config)
        except Exception:
            logger.warning("Could not create OCI Monitoring client for resource metrics", exc_info=True)
            return snapshots

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=IDLE_CPU_LOOKBACK_DAYS)

        _metric_map = {
            "AutonomousDB": [
                ("oci_autonomous_database", "CpuUtilization[1d].mean()", "avg_cpu_percent"),
                ("oci_autonomous_database", "Sessions[1d].mean()", "avg_sessions"),
            ],
            "LoadBalancer": [
                ("oci_lbaas", "HttpRequests[1d].sum()", "total_requests"),
            ],
        }

        for snap in snapshots:
            metrics = _metric_map.get(snap.service)
            if not metrics:
                continue
            for namespace, query_tpl, meta_key in metrics:
                try:
                    query = query_tpl.replace("resourceId", snap.resource_id)
                    if "{" not in query:
                        query = query.replace("[", f'{{resourceId = "{snap.resource_id}"}}[', 1)
                    resp = monitoring.summarize_metrics_data(
                        compartment_id=self._compartment_id,
                        summarize_metrics_data_details=oci.monitoring.models.SummarizeMetricsDataDetails(
                            namespace=namespace,
                            query=query,
                            start_time=start.isoformat(),
                            end_time=now.isoformat(),
                        ),
                    )
                    values: list[float] = []
                    for ts in resp.data:
                        for dp in ts.aggregated_datapoints:
                            if dp.value is not None:
                                values.append(float(dp.value))
                    if values:
                        if "total" in meta_key:
                            snap.metadata[meta_key] = round(sum(values))
                        else:
                            snap.metadata[meta_key] = round(sum(values) / len(values), 2)
                except Exception:
                    logger.debug("OCI metric %s failed for %s", query_tpl, snap.resource_id, exc_info=True)

        return snapshots
