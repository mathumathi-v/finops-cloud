# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import UTC, datetime
from typing import Any

import boto3

from cost_model.models import ResourceSnapshot

logger = logging.getLogger(__name__)


class AWSResourceCollector:
    """Fetches live resource metadata from AWS (EC2, EBS, ELB, NAT Gateway, EKS)."""

    def __init__(self, session: boto3.Session, account_id: str, regions: list[str]) -> None:
        self._session = session
        self._account_id = account_id
        self._regions = regions

    def collect_resources(self) -> list[ResourceSnapshot]:
        """Collect resources across all configured regions."""
        snapshots: list[ResourceSnapshot] = []
        collectors = [
            ("EC2", self._collect_ec2),
            ("EBS", self._collect_ebs),
            ("ELB", self._collect_elb),
            ("NAT Gateway", self._collect_nat_gateways),
            ("EKS", self._collect_eks),
        ]
        for region in self._regions:
            for name, collect_fn in collectors:
                try:
                    snapshots.extend(collect_fn(region))
                except Exception:
                    logger.warning(
                        "Failed to collect %s resources in %s (insufficient permissions?)",
                        name,
                        region,
                        exc_info=True,
                    )
        logger.info("Collected %d total AWS resources", len(snapshots))
        return snapshots

    def _get_name_tag(self, tags: list[Any] | None) -> str:
        if not tags:
            return ""
        for tag in tags:
            if tag.get("Key") == "Name":
                return tag.get("Value", "")
        return ""

    def _flatten_tags(self, tags: list[Any] | None) -> dict[str, str]:
        if not tags:
            return {}
        return {t["Key"]: t["Value"] for t in tags}

    # -- EC2 -------------------------------------------------------------------

    def _collect_ec2(self, region: str) -> list[ResourceSnapshot]:
        ec2 = self._session.client("ec2", region_name=region)
        snapshots: list[ResourceSnapshot] = []
        paginator = ec2.get_paginator("describe_instances")

        for page in paginator.paginate():
            for reservation in page.get("Reservations", []):
                for inst in reservation.get("Instances", []):
                    state = inst["State"]["Name"]
                    instance_type = inst.get("InstanceType", "unknown")
                    tags = inst.get("Tags", [])

                    snapshots.append(
                        ResourceSnapshot(
                            resource_id=inst["InstanceId"],
                            provider="aws",
                            account_id=self._account_id,
                            type="compute",
                            service="EC2",
                            name=self._get_name_tag(tags),
                            region=region,
                            daily_cost=0.0,
                            monthly_cost_estimate=0.0,
                            currency="USD",
                            state=state,
                            tags=self._flatten_tags(tags),
                            metadata={
                                "instance_type": instance_type,
                                "launch_time": inst.get("LaunchTime", ""),
                                "vpc_id": inst.get("VpcId", ""),
                            },
                            snapshot_time=datetime.now(UTC),
                        )
                    )

        logger.info("Collected %d EC2 instances in %s", len(snapshots), region)
        return snapshots

    # -- EBS -------------------------------------------------------------------

    def _collect_ebs(self, region: str) -> list[ResourceSnapshot]:
        ec2 = self._session.client("ec2", region_name=region)
        snapshots: list[ResourceSnapshot] = []
        paginator = ec2.get_paginator("describe_volumes")

        for page in paginator.paginate():
            for vol in page.get("Volumes", []):
                attachments = vol.get("Attachments", [])
                state = "attached" if attachments else "unattached"
                tags = vol.get("Tags", [])

                snapshots.append(
                    ResourceSnapshot(
                        resource_id=vol["VolumeId"],
                        provider="aws",
                        account_id=self._account_id,
                        type="storage",
                        service="EBS",
                        name=self._get_name_tag(tags),
                        region=region,
                        daily_cost=0.0,
                        monthly_cost_estimate=0.0,
                        currency="USD",
                        state=state,
                        tags=self._flatten_tags(tags),
                        metadata={
                            "volume_type": vol.get("VolumeType", ""),
                            "size_gb": vol.get("Size", 0),
                            "iops": vol.get("Iops", 0),
                            "encrypted": vol.get("Encrypted", False),
                        },
                        snapshot_time=datetime.now(UTC),
                    )
                )

        logger.info("Collected %d EBS volumes in %s", len(snapshots), region)
        return snapshots

    # -- ELB/ALB ---------------------------------------------------------------

    def _collect_elb(self, region: str) -> list[ResourceSnapshot]:
        elbv2 = self._session.client("elbv2", region_name=region)
        snapshots: list[ResourceSnapshot] = []
        paginator = elbv2.get_paginator("describe_load_balancers")

        for page in paginator.paginate():
            for lb in page.get("LoadBalancers", []):
                state = lb.get("State", {}).get("Code", "unknown")

                snapshots.append(
                    ResourceSnapshot(
                        resource_id=lb["LoadBalancerArn"],
                        provider="aws",
                        account_id=self._account_id,
                        type="network",
                        service="ELB",
                        name=lb.get("LoadBalancerName", ""),
                        region=region,
                        daily_cost=0.0,
                        monthly_cost_estimate=0.0,
                        currency="USD",
                        state=state,
                        tags={},
                        metadata={
                            "type": lb.get("Type", ""),
                            "scheme": lb.get("Scheme", ""),
                            "dns_name": lb.get("DNSName", ""),
                            "vpc_id": lb.get("VpcId", ""),
                        },
                        snapshot_time=datetime.now(UTC),
                    )
                )

        logger.info("Collected %d load balancers in %s", len(snapshots), region)
        return snapshots

    # -- NAT Gateway -----------------------------------------------------------

    def _collect_nat_gateways(self, region: str) -> list[ResourceSnapshot]:
        ec2 = self._session.client("ec2", region_name=region)
        snapshots: list[ResourceSnapshot] = []
        paginator = ec2.get_paginator("describe_nat_gateways")

        for page in paginator.paginate():
            for nat in page.get("NatGateways", []):
                state = nat.get("State", "unknown")
                tags = nat.get("Tags", [])

                snapshots.append(
                    ResourceSnapshot(
                        resource_id=nat["NatGatewayId"],
                        provider="aws",
                        account_id=self._account_id,
                        type="network",
                        service="NAT Gateway",
                        name=self._get_name_tag(tags),
                        region=region,
                        daily_cost=0.0,
                        monthly_cost_estimate=0.0,
                        currency="USD",
                        state=state,
                        tags=self._flatten_tags(tags),
                        metadata={
                            "subnet_id": nat.get("SubnetId", ""),
                            "vpc_id": nat.get("VpcId", ""),
                            "connectivity_type": nat.get("ConnectivityType", ""),
                        },
                        snapshot_time=datetime.now(UTC),
                    )
                )

        logger.info("Collected %d NAT gateways in %s", len(snapshots), region)
        return snapshots

    # -- EKS -------------------------------------------------------------------

    def _collect_eks(self, region: str) -> list[ResourceSnapshot]:
        eks = self._session.client("eks", region_name=region)
        snapshots: list[ResourceSnapshot] = []

        clusters_resp = eks.list_clusters()
        for cluster_name in clusters_resp.get("clusters", []):
            cluster = eks.describe_cluster(name=cluster_name)["cluster"]
            tags = cluster.get("tags", {})

            snapshots.append(
                ResourceSnapshot(
                    resource_id=cluster["arn"],
                    provider="aws",
                    account_id=self._account_id,
                    type="kubernetes",
                    service="EKS",
                    name=cluster_name,
                    region=region,
                    daily_cost=0.0,
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state=cluster.get("status", "unknown").lower(),
                    tags=tags,
                    metadata={
                        "version": cluster.get("version", ""),
                        "platform_version": cluster.get("platformVersion", ""),
                        "endpoint": cluster.get("endpoint", ""),
                    },
                    snapshot_time=datetime.now(UTC),
                )
            )

            # Collect nodegroups for the cluster
            nodegroups_resp = eks.list_nodegroups(clusterName=cluster_name)
            for ng_name in nodegroups_resp.get("nodegroups", []):
                ng = eks.describe_nodegroup(
                    clusterName=cluster_name, nodegroupName=ng_name
                )["nodegroup"]

                instance_types = ng.get("instanceTypes", [])
                scaling = ng.get("scalingConfig", {})

                snapshots.append(
                    ResourceSnapshot(
                        resource_id=ng["nodegroupArn"],
                        provider="aws",
                        account_id=self._account_id,
                        type="kubernetes",
                        service="EKS",
                        name=f"{cluster_name}/{ng_name}",
                        region=region,
                        daily_cost=0.0,
                        monthly_cost_estimate=0.0,
                        currency="USD",
                        state=ng.get("status", "unknown").lower(),
                        tags=ng.get("tags", {}),
                        metadata={
                            "instance_types": instance_types,
                            "desired_size": scaling.get("desiredSize", 0),
                            "min_size": scaling.get("minSize", 0),
                            "max_size": scaling.get("maxSize", 0),
                            "ami_type": ng.get("amiType", ""),
                            "capacity_type": ng.get("capacityType", ""),
                        },
                        snapshot_time=datetime.now(UTC),
                    )
                )

        logger.info("Collected %d EKS resources in %s", len(snapshots), region)
        return snapshots
