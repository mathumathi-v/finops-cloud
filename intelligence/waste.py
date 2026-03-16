# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cost_model.models import ResourceSnapshot
from intelligence.constants import STOPPED_INSTANCE_DAYS

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
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))


_DISK_SERVICES = {"EBS", "PersistentDisk", "ManagedDisk"}
_COMPUTE_SERVICES = {"EC2", "GCE", "VirtualMachine"}


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
    """Find compute instances that have been stopped for extended periods."""
    findings: list[WasteFinding] = []
    now = datetime.now(UTC)

    for r in resources:
        if r.service in _COMPUTE_SERVICES and r.state == "stopped":
            # Check if stopped longer than threshold based on snapshot age
            age_days = (now - r.snapshot_time).days if r.snapshot_time.tzinfo else 0
            # We flag it regardless — the CLI consumer can decide to filter by age
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
                        f"{r.service} instance {r.name or r.resource_id} is stopped. "
                        f"Stopped instances may still incur disk charges. "
                        f"Consider terminating if unused for > {STOPPED_INSTANCE_DAYS} days."
                    ),
                    estimated_monthly_savings=r.monthly_cost_estimate,
                    metadata={
                        "instance_type": (
                            r.metadata.get("instance_type")
                            or r.metadata.get("machine_type")
                            or r.metadata.get("vm_size")
                        ),
                        "days_stopped_approx": age_days,
                    },
                )
            )

    logger.info("Found %d stopped instance(s)", len(findings))
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


def find_all_waste(resources: list[ResourceSnapshot]) -> list[WasteFinding]:
    """Run all waste detection rules and return combined findings."""
    findings: list[WasteFinding] = []
    findings.extend(detect_unattached_disks(resources))
    findings.extend(detect_stopped_instances(resources))
    findings.extend(detect_idle_nat_gateways(resources))
    findings.extend(detect_unused_elastic_ips(resources))
    return findings
