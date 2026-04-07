# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from cost_model.models import ResourceSnapshot
from intelligence.constants import (
    DECLINING_CPU_MIN_DROP_PERCENT,
    IDLE_CPU_LOOKBACK_DAYS,
    IDLE_CPU_P95_THRESHOLD,
    IDLE_CPU_PERCENT_THRESHOLD,
    OFFHOURS_IDLE_CPU_THRESHOLD,
    STOPPED_FIRST_SEEN_DAYS,
    STOPPED_INSTANCE_DAYS,
)

logger = logging.getLogger(__name__)


@dataclass
class WasteFinding:
    """A single waste detection finding with estimated savings."""

    resource_id: str
    provider: str
    account_id: str
    service: str
    region: str
    name: str
    waste_type: str
    description: str
    estimated_monthly_savings: float
    metadata: dict[str, object] = field(default_factory=dict)
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_DISK_SERVICES = {"EBS", "PersistentDisk", "ManagedDisk", "BlockVolume"}
_COMPUTE_SERVICES = {"EC2", "GCE", "VirtualMachine", "Compute"}
_DATABASE_SERVICES = {"RDS", "AzureSQL", "CloudSQL", "AutonomousDB", "CosmosDB"}
_SERVERLESS_SERVICES = {"Lambda", "FunctionApp", "CloudRun", "CloudFunctions"}


def detect_unattached_disks(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Find unattached storage volumes across all cloud providers."""
    findings: list[WasteFinding] = []
    for r in resources:
        if r.service in _DISK_SERVICES and r.state == "unattached":
            size_gb = r.metadata.get("size_gb", 0)
            # Use actual daily_cost if available, else estimate from size
            if r.monthly_cost_estimate > 0:
                estimated_savings = r.monthly_cost_estimate
            else:
                estimated_savings = (
                    float(size_gb) * 0.08 if isinstance(size_gb, (int, float)) else 0.0
                )

            findings.append(
                WasteFinding(
                    resource_id=r.resource_id,
                    provider=r.provider,
                    account_id=r.account_id,
                    service=r.service,
                    region=r.region,
                    name=r.name,
                    waste_type="unattached_disk",
                    description=(
                        f"{r.service} volume {r.name or r.resource_id} ({size_gb} GB) "
                        f"is unattached. Consider snapshotting and deleting."
                    ),
                    estimated_monthly_savings=round(estimated_savings, 2),
                    metadata={
                        "size_gb": size_gb,
                        "disk_type": r.metadata.get("volume_type") or r.metadata.get("disk_type"),
                    },
                )
            )

    logger.info("Found %d unattached disk(s)", len(findings))
    return findings


def detect_stopped_instances(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Find compute instances that have been stopped for extended periods.

    Uses ``metadata["stopped_days"]`` if the collector provides it (e.g. from
    cloud provider state-change timestamps).  Otherwise falls back to the
    ``metadata["stopped_since"]`` ISO date or the snapshot age as a rough
    proxy.  Only flags instances stopped longer than ``STOPPED_FIRST_SEEN_DAYS``.
    """
    findings: list[WasteFinding] = []
    now = datetime.now(timezone.utc)

    for r in resources:
        if r.service not in _COMPUTE_SERVICES or r.state != "stopped":
            continue

        # Determine how long stopped — try multiple sources
        stopped_days: int | None = None
        raw = r.metadata.get("stopped_days")
        if raw is not None:
            try:
                stopped_days = int(raw)
            except (TypeError, ValueError):
                pass

        if stopped_days is None:
            stopped_since = r.metadata.get("stopped_since")
            if stopped_since:
                try:
                    since_dt = datetime.fromisoformat(str(stopped_since))
                    if since_dt.tzinfo is None:
                        since_dt = since_dt.replace(tzinfo=timezone.utc)
                    stopped_days = (now - since_dt).days
                except (TypeError, ValueError):
                    pass

        if stopped_days is None:
            # Fallback: snapshot age
            stopped_days = (now - r.snapshot_time).days if r.snapshot_time.tzinfo else 0

        # Only flag if stopped long enough
        if stopped_days < STOPPED_FIRST_SEEN_DAYS:
            continue

        if stopped_days > 30:
            urgency = "strongly recommend terminating"
        elif stopped_days > 14:
            urgency = "consider terminating soon"
        else:
            urgency = "review whether still needed"

        findings.append(
            WasteFinding(
                resource_id=r.resource_id,
                provider=r.provider,
                account_id=r.account_id,
                service=r.service,
                region=r.region,
                name=r.name,
                waste_type="stopped_instance",
                description=(
                    f"{r.service} instance {r.name or r.resource_id} has been stopped "
                    f"for ~{stopped_days} days. Stopped instances still incur disk charges. "
                    f"Action: {urgency}."
                ),
                estimated_monthly_savings=r.monthly_cost_estimate,
                metadata={
                    "instance_type": (
                        r.metadata.get("instance_type")
                        or r.metadata.get("machine_type")
                        or r.metadata.get("vm_size")
                    ),
                    "days_stopped": stopped_days,
                },
            )
        )

    logger.info("Found %d stopped instance(s) (> %d days)", len(findings), STOPPED_FIRST_SEEN_DAYS)
    return findings


def detect_idle_nat_gateways(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Find NAT Gateways that may be idle (low or no traffic)."""
    findings: list[WasteFinding] = []
    # NAT Gateway costs ~$0.045/hour = ~$32.40/month just for existing
    nat_monthly_base_cost = 32.40

    for r in resources:
        if r.service == "NAT Gateway" and r.state == "available":
            findings.append(
                WasteFinding(
                    resource_id=r.resource_id,
                    provider=r.provider,
                    account_id=r.account_id,
                    service=r.service,
                    region=r.region,
                    name=r.name,
                    waste_type="idle_nat",
                    description=(
                        f"NAT Gateway {r.resource_id} is active. "
                        f"NAT Gateways cost ~${nat_monthly_base_cost}/month even with no traffic. "
                        f"Verify it is actively used."
                    ),
                    estimated_monthly_savings=nat_monthly_base_cost,
                    metadata={"subnet_id": r.metadata.get("subnet_id")},
                )
            )

    logger.info("Found %d potentially idle NAT gateway(s)", len(findings))
    return findings


def detect_unused_elastic_ips(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Find Elastic IPs not attached to a running instance."""
    findings: list[WasteFinding] = []
    # Unattached EIP costs $0.005/hour = ~$3.60/month
    eip_monthly_cost = 3.60

    for r in resources:
        if r.service == "ElasticIP" and r.state == "unattached":
            findings.append(
                WasteFinding(
                    resource_id=r.resource_id,
                    provider=r.provider,
                    account_id=r.account_id,
                    service=r.service,
                    region=r.region,
                    name=r.name,
                    waste_type="unused_ip",
                    description=(
                        f"Elastic IP {r.resource_id} is not attached to a running instance. "
                        f"Unattached EIPs cost ~${eip_monthly_cost}/month."
                    ),
                    estimated_monthly_savings=eip_monthly_cost,
                    metadata={},
                )
            )

    logger.info("Found %d unused Elastic IP(s)", len(findings))
    return findings


def detect_idle_instances(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Find running compute instances with very low CPU utilization.

    Uses pattern-based analysis when richer metrics are available:
    - **Average + P95 check**: flags only when *both* the average AND the
      95th-percentile daily CPU are below their respective thresholds,
      preventing false positives from servers with periodic batch spikes.
    - Falls back to average-only check when P95 data is unavailable.

    Requires ``metadata["avg_cpu_percent"]`` to be populated by the
    provider's ``collect_cpu_metrics()`` method.  Resources without
    this key are silently skipped.
    """
    findings: list[WasteFinding] = []
    for r in resources:
        if r.service not in _COMPUTE_SERVICES or r.state != "running":
            continue
        avg_cpu = r.metadata.get("avg_cpu_percent")
        if avg_cpu is None:
            continue
        try:
            cpu_val = float(avg_cpu)
        except (TypeError, ValueError):
            continue

        p95_cpu = r.metadata.get("p95_cpu_percent")
        max_cpu = r.metadata.get("max_cpu_percent")

        # Pattern-based: require BOTH avg AND p95 to be low
        if p95_cpu is not None:
            try:
                p95_val = float(p95_cpu)
            except (TypeError, ValueError):
                p95_val = None
            if p95_val is not None and (cpu_val >= IDLE_CPU_PERCENT_THRESHOLD or p95_val >= IDLE_CPU_P95_THRESHOLD):
                continue
        else:
            # Fallback: average-only check
            if cpu_val >= IDLE_CPU_PERCENT_THRESHOLD:
                continue

        detail_parts = [
            f"avg CPU {cpu_val:.1f}%",
        ]
        if p95_cpu is not None:
            detail_parts.append(f"P95 {float(p95_cpu):.1f}%")
        if max_cpu is not None:
            detail_parts.append(f"peak {float(max_cpu):.1f}%")

        findings.append(
            WasteFinding(
                resource_id=r.resource_id,
                provider=r.provider,
                account_id=r.account_id,
                service=r.service,
                region=r.region,
                name=r.name,
                waste_type="idle_instance",
                description=(
                    f"{r.service} instance {r.name or r.resource_id} is idle: "
                    f"{', '.join(detail_parts)} over the last "
                    f"{IDLE_CPU_LOOKBACK_DAYS} days. Consider downsizing or terminating."
                ),
                estimated_monthly_savings=r.monthly_cost_estimate,
                metadata={
                    "avg_cpu_percent": cpu_val,
                    "p95_cpu_percent": p95_cpu,
                    "max_cpu_percent": max_cpu,
                    "lookback_days": IDLE_CPU_LOOKBACK_DAYS,
                    "instance_type": (
                        r.metadata.get("instance_type")
                        or r.metadata.get("machine_type")
                        or r.metadata.get("vm_size")
                    ),
                },
            )
        )

    logger.info("Found %d idle instance(s) (avg < %.1f%% AND P95 < %.1f%%)",
                len(findings), IDLE_CPU_PERCENT_THRESHOLD, IDLE_CPU_P95_THRESHOLD)
    return findings


def detect_offhours_only_idle(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Find instances that are idle on weekends/off-hours but busy on weekdays.

    These are candidates for scheduling (auto-stop on nights/weekends) rather
    than termination.  Requires ``cpu_weekday_avg`` and ``cpu_weekend_avg``
    in metadata (populated by the enriched collectors).
    """
    findings: list[WasteFinding] = []
    for r in resources:
        if r.service not in _COMPUTE_SERVICES or r.state != "running":
            continue
        weekday_avg = r.metadata.get("cpu_weekday_avg")
        weekend_avg = r.metadata.get("cpu_weekend_avg")
        if weekday_avg is None or weekend_avg is None:
            continue
        try:
            wd = float(weekday_avg)
            we = float(weekend_avg)
        except (TypeError, ValueError):
            continue

        # Busy on weekdays but idle on weekends
        if wd >= IDLE_CPU_PERCENT_THRESHOLD and we < OFFHOURS_IDLE_CPU_THRESHOLD:
            # Estimate savings: ~2/7 of monthly cost (weekends)
            weekend_savings = round(r.monthly_cost_estimate * (2 / 7), 2)
            findings.append(
                WasteFinding(
                    resource_id=r.resource_id,
                    provider=r.provider,
                    account_id=r.account_id,
                    service=r.service,
                    region=r.region,
                    name=r.name,
                    waste_type="offhours_idle",
                    description=(
                        f"{r.service} instance {r.name or r.resource_id} is busy on weekdays "
                        f"(avg {wd:.1f}%) but idle on weekends (avg {we:.1f}%). "
                        f"Consider scheduling auto-stop on weekends to save ~${weekend_savings}/mo."
                    ),
                    estimated_monthly_savings=weekend_savings,
                    metadata={
                        "cpu_weekday_avg": wd,
                        "cpu_weekend_avg": we,
                        "instance_type": (
                            r.metadata.get("instance_type")
                            or r.metadata.get("machine_type")
                            or r.metadata.get("vm_size")
                        ),
                    },
                )
            )

    logger.info("Found %d off-hours idle instance(s)", len(findings))
    return findings


def detect_declining_usage(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Find running instances where CPU utilisation is trending downward.

    A declining trend suggests the workload is shrinking and the instance
    may be over-provisioned or becoming unused.  Requires
    ``cpu_trend_direction`` and ``cpu_daily_values`` in metadata.
    """
    findings: list[WasteFinding] = []
    for r in resources:
        if r.service not in _COMPUTE_SERVICES or r.state != "running":
            continue
        trend = r.metadata.get("cpu_trend_direction")
        if trend != "declining":
            continue
        daily_vals = r.metadata.get("cpu_daily_values")
        if not daily_vals or len(daily_vals) < 4:
            continue

        avgs = [d["avg"] for d in daily_vals]
        mid = len(avgs) // 2
        first_half_avg = sum(avgs[:mid]) / mid
        second_half_avg = sum(avgs[mid:]) / (len(avgs) - mid)

        if first_half_avg == 0:
            continue
        drop_pct = ((first_half_avg - second_half_avg) / first_half_avg) * 100
        if drop_pct < DECLINING_CPU_MIN_DROP_PERCENT:
            continue

        findings.append(
            WasteFinding(
                resource_id=r.resource_id,
                provider=r.provider,
                account_id=r.account_id,
                service=r.service,
                region=r.region,
                name=r.name,
                waste_type="declining_usage",
                description=(
                    f"{r.service} instance {r.name or r.resource_id} CPU usage has declined "
                    f"{drop_pct:.0f}% over the last {IDLE_CPU_LOOKBACK_DAYS} days "
                    f"(from {first_half_avg:.1f}% → {second_half_avg:.1f}%). "
                    f"Workload may be shrinking. Consider downsizing."
                ),
                estimated_monthly_savings=round(r.monthly_cost_estimate * (drop_pct / 100) * 0.5, 2),
                metadata={
                    "first_half_avg_cpu": round(first_half_avg, 2),
                    "second_half_avg_cpu": round(second_half_avg, 2),
                    "drop_percent": round(drop_pct, 1),
                    "instance_type": (
                        r.metadata.get("instance_type")
                        or r.metadata.get("machine_type")
                        or r.metadata.get("vm_size")
                    ),
                },
            )
        )

    logger.info("Found %d instance(s) with declining usage", len(findings))
    return findings


def detect_idle_databases(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Find database instances that are stopped or have very low activity."""
    findings: list[WasteFinding] = []
    for r in resources:
        if r.service in _DATABASE_SERVICES and r.state == "stopped":
            findings.append(
                WasteFinding(
                    resource_id=r.resource_id,
                    provider=r.provider,
                    account_id=r.account_id,
                    service=r.service,
                    region=r.region,
                    name=r.name,
                    waste_type="stopped_database",
                    description=(
                        f"{r.service} database {r.name or r.resource_id} is stopped. "
                        f"Stopped databases may still incur storage charges. "
                        f"Consider deleting if no longer needed."
                    ),
                    estimated_monthly_savings=r.monthly_cost_estimate,
                    metadata={
                        "engine": r.metadata.get("engine") or r.metadata.get("database_version", ""),
                    },
                )
            )
    logger.info("Found %d idle database(s)", len(findings))
    return findings


def detect_idle_databases_by_metrics(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Find running databases with near-zero connections or CPU over the lookback window.

    Requires ``avg_connections`` or ``avg_cpu_percent`` in metadata
    (populated by ``collect_resource_metrics``).
    """
    findings: list[WasteFinding] = []
    for r in resources:
        if r.service not in _DATABASE_SERVICES or r.state in ("stopped", "terminated"):
            continue

        avg_conn = r.metadata.get("avg_connections")
        max_conn = r.metadata.get("max_connections")
        avg_cpu = r.metadata.get("avg_cpu_percent")
        avg_dtu = r.metadata.get("avg_dtu_percent")
        avg_sessions = r.metadata.get("avg_sessions")

        # Determine if idle via connections
        conn_idle = False
        if avg_conn is not None:
            try:
                conn_idle = float(avg_conn) < 1.0
            except (TypeError, ValueError):
                pass
        elif avg_sessions is not None:
            try:
                conn_idle = float(avg_sessions) < 1.0
            except (TypeError, ValueError):
                pass

        # Determine if idle via CPU/DTU
        cpu_idle = False
        cpu_val = avg_dtu if avg_dtu is not None else avg_cpu
        if cpu_val is not None:
            try:
                cpu_idle = float(cpu_val) < IDLE_CPU_PERCENT_THRESHOLD
            except (TypeError, ValueError):
                pass

        if not conn_idle and not cpu_idle:
            continue

        detail_parts = []
        if avg_conn is not None:
            detail_parts.append(f"avg connections: {float(avg_conn):.1f}")
        if max_conn is not None:
            detail_parts.append(f"peak connections: {float(max_conn):.0f}")
        if avg_sessions is not None:
            detail_parts.append(f"avg sessions: {float(avg_sessions):.1f}")
        if cpu_val is not None:
            label = "DTU" if avg_dtu is not None else "CPU"
            detail_parts.append(f"avg {label}: {float(cpu_val):.1f}%")

        findings.append(
            WasteFinding(
                resource_id=r.resource_id,
                provider=r.provider,
                account_id=r.account_id,
                service=r.service,
                region=r.region,
                name=r.name,
                waste_type="idle_database",
                description=(
                    f"{r.service} database {r.name or r.resource_id} appears idle over "
                    f"the last {IDLE_CPU_LOOKBACK_DAYS} days ({', '.join(detail_parts)}). "
                    f"Consider downsizing or deleting."
                ),
                estimated_monthly_savings=r.monthly_cost_estimate,
                metadata={k: r.metadata.get(k) for k in (
                    "avg_connections", "max_connections", "avg_cpu_percent",
                    "avg_dtu_percent", "avg_sessions", "engine",
                )},
            )
        )

    logger.info("Found %d idle database(s) by metrics", len(findings))
    return findings


def detect_idle_load_balancers(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Find load balancers with zero or near-zero traffic over the lookback window.

    Requires ``total_requests`` or ``avg_daily_requests`` or ``total_bytes``
    in metadata.
    """
    _LB_SERVICES = {"ELB", "LoadBalancer"}
    findings: list[WasteFinding] = []
    for r in resources:
        if r.service not in _LB_SERVICES:
            continue

        total_req = r.metadata.get("total_requests")
        total_bytes = r.metadata.get("total_bytes")
        avg_daily = r.metadata.get("avg_daily_requests")
        zero_days = r.metadata.get("zero_traffic_days")

        is_idle = False
        if total_req is not None:
            is_idle = float(total_req) == 0
        elif total_bytes is not None:
            is_idle = float(total_bytes) == 0
        elif avg_daily is not None:
            is_idle = float(avg_daily) < 1.0

        if not is_idle:
            continue

        detail_parts = []
        if total_req is not None:
            detail_parts.append(f"total requests: {int(total_req)}")
        if zero_days is not None:
            detail_parts.append(f"{int(zero_days)} zero-traffic days")
        if total_bytes is not None:
            detail_parts.append(f"total bytes: {int(total_bytes)}")

        findings.append(
            WasteFinding(
                resource_id=r.resource_id,
                provider=r.provider,
                account_id=r.account_id,
                service=r.service,
                region=r.region,
                name=r.name,
                waste_type="idle_load_balancer",
                description=(
                    f"Load balancer {r.name or r.resource_id} has zero traffic over "
                    f"the last {IDLE_CPU_LOOKBACK_DAYS} days ({', '.join(detail_parts)}). "
                    f"Consider deleting if no longer needed."
                ),
                estimated_monthly_savings=r.monthly_cost_estimate,
                metadata={k: r.metadata.get(k) for k in (
                    "total_requests", "avg_daily_requests", "zero_traffic_days", "total_bytes",
                )},
            )
        )

    logger.info("Found %d idle load balancer(s)", len(findings))
    return findings


def detect_idle_nat_by_traffic(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Find NAT Gateways with zero or near-zero actual traffic from CloudWatch.

    Upgrades the state-only ``detect_idle_nat_gateways`` check with real
    traffic data when ``avg_daily_gb`` is available in metadata.
    """
    nat_monthly_base_cost = 32.40
    findings: list[WasteFinding] = []
    for r in resources:
        if r.service != "NAT Gateway":
            continue

        avg_gb = r.metadata.get("avg_daily_gb")
        zero_days = r.metadata.get("zero_traffic_days")
        total_bytes = r.metadata.get("total_bytes_processed")

        if avg_gb is None:
            continue  # no traffic data — handled by state-only detector

        try:
            if float(avg_gb) >= 0.001:  # > 1 MB/day = not idle
                continue
        except (TypeError, ValueError):
            continue

        detail_parts = [f"avg {float(avg_gb):.4f} GB/day"]
        if zero_days is not None:
            detail_parts.append(f"{int(zero_days)} zero-traffic days")

        findings.append(
            WasteFinding(
                resource_id=r.resource_id,
                provider=r.provider,
                account_id=r.account_id,
                service=r.service,
                region=r.region,
                name=r.name,
                waste_type="idle_nat_confirmed",
                description=(
                    f"NAT Gateway {r.name or r.resource_id} has near-zero traffic over "
                    f"the last {IDLE_CPU_LOOKBACK_DAYS} days ({', '.join(detail_parts)}). "
                    f"Costs ~${nat_monthly_base_cost}/month. Delete if unused."
                ),
                estimated_monthly_savings=nat_monthly_base_cost,
                metadata={k: r.metadata.get(k) for k in (
                    "avg_daily_gb", "zero_traffic_days", "total_bytes_processed",
                )},
            )
        )

    logger.info("Found %d traffic-confirmed idle NAT gateway(s)", len(findings))
    return findings


def detect_idle_attached_disks(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Find attached EBS/disk volumes with zero I/O over the lookback window.

    These are volumes that are attached but never read from or written to.
    Requires ``avg_daily_iops`` in metadata.
    """
    findings: list[WasteFinding] = []
    for r in resources:
        if r.service not in _DISK_SERVICES or r.state == "unattached":
            continue

        avg_iops = r.metadata.get("avg_daily_iops")
        if avg_iops is None:
            continue
        try:
            if float(avg_iops) > 0:
                continue
        except (TypeError, ValueError):
            continue

        findings.append(
            WasteFinding(
                resource_id=r.resource_id,
                provider=r.provider,
                account_id=r.account_id,
                service=r.service,
                region=r.region,
                name=r.name,
                waste_type="idle_disk",
                description=(
                    f"{r.service} volume {r.name or r.resource_id} is attached but has zero "
                    f"read/write ops over the last {IDLE_CPU_LOOKBACK_DAYS} days. "
                    f"Consider detaching and snapshotting."
                ),
                estimated_monthly_savings=r.monthly_cost_estimate,
                metadata={
                    "total_iops": r.metadata.get("total_iops"),
                    "size_gb": r.metadata.get("size_gb"),
                    "volume_type": r.metadata.get("volume_type") or r.metadata.get("disk_type"),
                },
            )
        )

    logger.info("Found %d idle attached disk(s)", len(findings))
    return findings


def detect_zero_invocation_functions(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Find Lambda/Cloud Functions/Function Apps with zero invocations.

    Requires ``total_invocations`` in metadata.
    """
    findings: list[WasteFinding] = []
    for r in resources:
        if r.service not in _SERVERLESS_SERVICES:
            continue

        total_inv = r.metadata.get("total_invocations")
        if total_inv is None:
            continue
        try:
            if int(total_inv) > 0:
                continue
        except (TypeError, ValueError):
            continue

        findings.append(
            WasteFinding(
                resource_id=r.resource_id,
                provider=r.provider,
                account_id=r.account_id,
                service=r.service,
                region=r.region,
                name=r.name,
                waste_type="zero_invocation_function",
                description=(
                    f"{r.service} function {r.name or r.resource_id} has zero invocations "
                    f"over the last {IDLE_CPU_LOOKBACK_DAYS} days. "
                    f"Consider deleting if no longer needed."
                ),
                estimated_monthly_savings=r.monthly_cost_estimate,
                metadata={
                    "total_invocations": 0,
                    "total_errors": r.metadata.get("total_errors"),
                    "runtime": r.metadata.get("runtime"),
                },
            )
        )

    logger.info("Found %d zero-invocation function(s)", len(findings))
    return findings


def find_all_waste(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Run all waste detection rules and return combined findings.

    Rules are grouped into state-based and pattern-based detectors.
    Pattern-based detectors require metrics enrichment from
    ``collect_resource_metrics()``.
    """
    findings: list[WasteFinding] = []

    # --- State-based detectors (work with snapshot data only) ---
    findings.extend(detect_unattached_disks(resources))
    findings.extend(detect_stopped_instances(resources))
    findings.extend(detect_unused_elastic_ips(resources))
    findings.extend(detect_idle_databases(resources))

    # --- Pattern-based detectors (require metrics enrichment) ---
    # Compute
    findings.extend(detect_idle_instances(resources))
    findings.extend(detect_offhours_only_idle(resources))
    findings.extend(detect_declining_usage(resources))
    # Databases
    findings.extend(detect_idle_databases_by_metrics(resources))
    # Networking
    findings.extend(detect_idle_load_balancers(resources))
    findings.extend(detect_idle_nat_by_traffic(resources))
    # NAT fallback: state-only check for NATs without traffic data
    nat_confirmed_ids = {f.resource_id for f in findings if f.waste_type == "idle_nat_confirmed"}
    nat_state_findings = detect_idle_nat_gateways(resources)
    findings.extend(f for f in nat_state_findings if f.resource_id not in nat_confirmed_ids)
    # Storage
    findings.extend(detect_idle_attached_disks(resources))
    # Serverless
    findings.extend(detect_zero_invocation_functions(resources))

    return findings
