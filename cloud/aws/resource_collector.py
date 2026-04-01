# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3

from cloud.pricing import PricingProvider
from cost_model.models import ResourceSnapshot
from intelligence.constants import IDLE_CPU_LOOKBACK_DAYS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AWS pricing tables (us-east-1, on-demand, Linux).  Used only when billing
# data is not available per-resource.
# ---------------------------------------------------------------------------

_EC2_HOURLY_USD: dict[str, float] = {
    # T3 burstable
    "t3.nano": 0.0052, "t3.micro": 0.0104, "t3.small": 0.0208,
    "t3.medium": 0.0416, "t3.large": 0.0832, "t3.xlarge": 0.1664,
    "t3.2xlarge": 0.3328,
    # T3a (AMD)
    "t3a.nano": 0.0047, "t3a.micro": 0.0094, "t3a.small": 0.0188,
    "t3a.medium": 0.0376, "t3a.large": 0.0752, "t3a.xlarge": 0.1504,
    "t3a.2xlarge": 0.3008,
    # M5 general purpose
    "m5.large": 0.096, "m5.xlarge": 0.192, "m5.2xlarge": 0.384,
    "m5.4xlarge": 0.768, "m5.8xlarge": 1.536, "m5.12xlarge": 2.304,
    "m5.16xlarge": 3.072, "m5.24xlarge": 4.608,
    # M6i
    "m6i.large": 0.096, "m6i.xlarge": 0.192, "m6i.2xlarge": 0.384,
    "m6i.4xlarge": 0.768, "m6i.8xlarge": 1.536,
    # C5 compute-optimised
    "c5.large": 0.085, "c5.xlarge": 0.17, "c5.2xlarge": 0.34,
    "c5.4xlarge": 0.68, "c5.9xlarge": 1.53, "c5.18xlarge": 3.06,
    # C6i
    "c6i.large": 0.085, "c6i.xlarge": 0.17, "c6i.2xlarge": 0.34,
    # R5 memory-optimised
    "r5.large": 0.126, "r5.xlarge": 0.252, "r5.2xlarge": 0.504,
    "r5.4xlarge": 1.008, "r5.8xlarge": 2.016,
    # R6i
    "r6i.large": 0.126, "r6i.xlarge": 0.252, "r6i.2xlarge": 0.504,
    # GPU
    "p3.2xlarge": 3.06, "p3.8xlarge": 12.24, "p3.16xlarge": 24.48,
    "g4dn.xlarge": 0.526, "g4dn.2xlarge": 0.752, "g4dn.4xlarge": 1.204,
}

_EBS_GB_MONTH_USD: dict[str, float] = {
    "gp2": 0.10, "gp3": 0.08, "io1": 0.125, "io2": 0.125,
    "st1": 0.045, "sc1": 0.015, "standard": 0.05,
}

_ELB_HOURLY_USD: dict[str, float] = {
    "application": 0.0225, "network": 0.0225, "gateway": 0.0125,
}

_NAT_HOURLY_USD = 0.045
_EKS_HOURLY_USD = 0.10

# RDS instance-class hourly USD (us-east-1, single-AZ, Linux).
_RDS_HOURLY_USD: dict[str, float] = {
    "db.t3.micro": 0.017, "db.t3.small": 0.034, "db.t3.medium": 0.068,
    "db.t3.large": 0.136, "db.t3.xlarge": 0.272, "db.t3.2xlarge": 0.544,
    "db.t4g.micro": 0.016, "db.t4g.small": 0.032, "db.t4g.medium": 0.065,
    "db.t4g.large": 0.129,
    "db.m5.large": 0.171, "db.m5.xlarge": 0.342, "db.m5.2xlarge": 0.684,
    "db.m5.4xlarge": 1.368,
    "db.m6g.large": 0.152, "db.m6g.xlarge": 0.304, "db.m6g.2xlarge": 0.608,
    "db.r5.large": 0.24, "db.r5.xlarge": 0.48, "db.r5.2xlarge": 0.96,
    "db.r6g.large": 0.218, "db.r6g.xlarge": 0.435, "db.r6g.2xlarge": 0.87,
}

_RDS_STORAGE_GB_MONTH_USD: dict[str, float] = {
    "gp2": 0.115, "gp3": 0.08, "io1": 0.125, "io2": 0.125,
    "standard": 0.10, "aurora": 0.10,
}

_HOURS_PER_DAY = 24.0
_DAYS_PER_MONTH = 30.0


def _daily_cost_for_ec2(
    instance_type: str, state: str, hourly_override: float | None = None,
) -> float:
    """Estimate daily cost for an EC2 instance from type and state."""
    if state in ("stopped", "stopping", "terminated", "shutting-down"):
        return 0.0
    hourly = hourly_override if hourly_override is not None else _EC2_HOURLY_USD.get(instance_type.lower(), 0.0)
    return round(hourly * _HOURS_PER_DAY, 6)


def _daily_cost_for_ebs(
    volume_type: str, size_gb: int, gb_month_override: float | None = None,
) -> float:
    """Estimate daily cost for an EBS volume from type and size."""
    monthly_per_gb = gb_month_override if gb_month_override is not None else _EBS_GB_MONTH_USD.get(volume_type.lower(), 0.08)
    return round(size_gb * monthly_per_gb / _DAYS_PER_MONTH, 6)


def _daily_cost_for_elb(lb_type: str) -> float:
    """Estimate daily base cost for a load balancer from type."""
    hourly = _ELB_HOURLY_USD.get(lb_type.lower(), 0.0225)
    return round(hourly * _HOURS_PER_DAY, 6)


def _daily_cost_for_nat() -> float:
    """Estimate daily base cost for a NAT Gateway."""
    return round(_NAT_HOURLY_USD * _HOURS_PER_DAY, 6)


def _daily_cost_for_eks() -> float:
    """Estimate daily control-plane cost for an EKS cluster."""
    return round(_EKS_HOURLY_USD * _HOURS_PER_DAY, 4)


def _daily_cost_for_rds(
    instance_class: str, storage_gb: int, storage_type: str, multi_az: bool,
    hourly_override: float | None = None,
) -> float:
    """Estimate daily cost for an RDS instance (compute + storage)."""
    hourly = hourly_override if hourly_override is not None else _RDS_HOURLY_USD.get(instance_class.lower(), 0.0)
    if multi_az:
        hourly *= 2
    compute_daily = hourly * _HOURS_PER_DAY
    storage_monthly_per_gb = _RDS_STORAGE_GB_MONTH_USD.get(storage_type.lower(), 0.08)
    storage_daily = storage_gb * storage_monthly_per_gb / _DAYS_PER_MONTH
    return round(compute_daily + storage_daily, 6)


class AWSResourceCollector:
    """Fetches live resource metadata from AWS."""

    def __init__(
        self,
        session: boto3.Session,
        account_id: str,
        regions: list[str],
        pricing_provider: PricingProvider | None = None,
    ) -> None:
        self._session = session
        self._account_id = account_id
        self._regions = regions
        self._pricing = pricing_provider

    def collect_resources(self) -> list[ResourceSnapshot]:
        """Collect resources across all configured regions."""
        snapshots: list[ResourceSnapshot] = []
        regional_collectors = [
            ("EC2", self._collect_ec2),
            ("EBS", self._collect_ebs),
            ("ELB", self._collect_elb),
            ("NAT Gateway", self._collect_nat_gateways),
            ("EKS", self._collect_eks),
            ("RDS", self._collect_rds),
            ("Lambda", self._collect_lambda),
            ("VPN", self._collect_vpn),
            ("API Gateway", self._collect_api_gateway),
        ]
        for region in self._regions:
            for name, collect_fn in regional_collectors:
                try:
                    snapshots.extend(collect_fn(region))
                except Exception:
                    logger.warning(
                        "Failed to collect %s resources in %s (insufficient permissions?)",
                        name,
                        region,
                        exc_info=True,
                    )

        # Global services (collect once)
        global_collectors = [
            ("S3", self._collect_s3),
            ("CloudFront", self._collect_cloudfront),
        ]
        for name, collect_fn in global_collectors:
            try:
                snapshots.extend(collect_fn())
            except Exception:
                logger.warning(
                    "Failed to collect %s resources (insufficient permissions?)",
                    name,
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

                    hourly = self._pricing.get_compute_hourly(instance_type, region) if self._pricing else None
                    daily = _daily_cost_for_ec2(instance_type, state, hourly_override=hourly)
                    snapshots.append(
                        ResourceSnapshot(
                            resource_id=inst["InstanceId"],
                            provider="aws",
                            account_id=self._account_id,
                            type="compute",
                            service="EC2",
                            name=self._get_name_tag(tags),
                            region=region,
                            daily_cost=daily,
                            monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
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

                vol_type = vol.get("VolumeType", "gp3")
                size_gb = vol.get("Size", 0)
                daily = _daily_cost_for_ebs(vol_type, size_gb)
                snapshots.append(
                    ResourceSnapshot(
                        resource_id=vol["VolumeId"],
                        provider="aws",
                        account_id=self._account_id,
                        type="storage",
                        service="EBS",
                        name=self._get_name_tag(tags),
                        region=region,
                        daily_cost=daily,
                        monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
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

                lb_type = lb.get("Type", "application")
                daily = _daily_cost_for_elb(lb_type)
                snapshots.append(
                    ResourceSnapshot(
                        resource_id=lb["LoadBalancerArn"],
                        provider="aws",
                        account_id=self._account_id,
                        type="network",
                        service="ELB",
                        name=lb.get("LoadBalancerName", ""),
                        region=region,
                        daily_cost=daily,
                        monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
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

                daily = _daily_cost_for_nat() if state == "available" else 0.0
                snapshots.append(
                    ResourceSnapshot(
                        resource_id=nat["NatGatewayId"],
                        provider="aws",
                        account_id=self._account_id,
                        type="network",
                        service="NAT Gateway",
                        name=self._get_name_tag(tags),
                        region=region,
                        daily_cost=daily,
                        monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
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

            cp_daily = _daily_cost_for_eks()
            snapshots.append(
                ResourceSnapshot(
                    resource_id=cluster["arn"],
                    provider="aws",
                    account_id=self._account_id,
                    type="kubernetes",
                    service="EKS",
                    name=cluster_name,
                    region=region,
                    daily_cost=cp_daily,
                    monthly_cost_estimate=round(cp_daily * _DAYS_PER_MONTH, 4),
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

                # Estimate cost from first instance type and desired size
                ng_instance_type = instance_types[0] if instance_types else ""
                desired = scaling.get("desiredSize", 0)
                daily_per_node = _daily_cost_for_ec2(ng_instance_type, "running")
                ng_daily = round(daily_per_node * desired, 4)

                snapshots.append(
                    ResourceSnapshot(
                        resource_id=ng["nodegroupArn"],
                        provider="aws",
                        account_id=self._account_id,
                        type="kubernetes",
                        service="EKS",
                        name=f"{cluster_name}/{ng_name}",
                        region=region,
                        daily_cost=ng_daily,
                        monthly_cost_estimate=round(ng_daily * _DAYS_PER_MONTH, 4),
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

    # -- RDS -------------------------------------------------------------------

    def _collect_rds(self, region: str) -> list[ResourceSnapshot]:
        rds = self._session.client("rds", region_name=region)
        snapshots: list[ResourceSnapshot] = []
        paginator = rds.get_paginator("describe_db_instances")

        for page in paginator.paginate():
            for inst in page.get("DBInstances", []):
                state = inst.get("DBInstanceStatus", "unknown")
                instance_class = inst.get("DBInstanceClass", "")
                storage_gb = inst.get("AllocatedStorage", 0)
                storage_type = inst.get("StorageType", "gp3")
                multi_az = inst.get("MultiAZ", False)
                tags = inst.get("TagList", [])

                daily = _daily_cost_for_rds(
                    instance_class, storage_gb, storage_type, multi_az,
                ) if state == "available" else 0.0

                snapshots.append(
                    ResourceSnapshot(
                        resource_id=inst.get("DBInstanceArn", inst["DBInstanceIdentifier"]),
                        provider="aws",
                        account_id=self._account_id,
                        type="database",
                        service="RDS",
                        name=inst.get("DBInstanceIdentifier", ""),
                        region=region,
                        daily_cost=daily,
                        monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                        currency="USD",
                        state=state,
                        tags=self._flatten_tags(tags),
                        metadata={
                            "engine": inst.get("Engine", ""),
                            "engine_version": inst.get("EngineVersion", ""),
                            "instance_class": instance_class,
                            "storage_gb": storage_gb,
                            "storage_type": storage_type,
                            "multi_az": multi_az,
                            "vpc_id": inst.get("DBSubnetGroup", {}).get("VpcId", ""),
                        },
                        snapshot_time=datetime.now(UTC),
                    )
                )

        logger.info("Collected %d RDS instances in %s", len(snapshots), region)
        return snapshots

    # -- S3 (global) -----------------------------------------------------------

    def _collect_s3(self) -> list[ResourceSnapshot]:
        s3 = self._session.client("s3")
        snapshots: list[ResourceSnapshot] = []

        buckets = s3.list_buckets().get("Buckets", [])
        for bucket in buckets:
            name = bucket["Name"]
            try:
                loc = s3.get_bucket_location(Bucket=name).get("LocationConstraint")
                bucket_region = loc or "us-east-1"
            except Exception:
                bucket_region = "unknown"

            snapshots.append(
                ResourceSnapshot(
                    resource_id=f"arn:aws:s3:::{name}",
                    provider="aws",
                    account_id=self._account_id,
                    type="storage",
                    service="S3",
                    name=name,
                    region=bucket_region,
                    daily_cost=0.0,  # size-dependent; use Cost Explorer
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state="active",
                    tags={},
                    metadata={
                        "creation_date": str(bucket.get("CreationDate", "")),
                    },
                    snapshot_time=datetime.now(UTC),
                )
            )

        logger.info("Collected %d S3 buckets", len(snapshots))
        return snapshots

    # -- Lambda ----------------------------------------------------------------

    def _collect_lambda(self, region: str) -> list[ResourceSnapshot]:
        lam = self._session.client("lambda", region_name=region)
        snapshots: list[ResourceSnapshot] = []
        paginator = lam.get_paginator("list_functions")

        for page in paginator.paginate():
            for fn in page.get("Functions", []):
                snapshots.append(
                    ResourceSnapshot(
                        resource_id=fn["FunctionArn"],
                        provider="aws",
                        account_id=self._account_id,
                        type="serverless",
                        service="Lambda",
                        name=fn.get("FunctionName", ""),
                        region=region,
                        daily_cost=0.0,  # invocation-based
                        monthly_cost_estimate=0.0,
                        currency="USD",
                        state="active",
                        tags={},
                        metadata={
                            "runtime": fn.get("Runtime", ""),
                            "memory_mb": fn.get("MemorySize", 0),
                            "timeout": fn.get("Timeout", 0),
                            "code_size": fn.get("CodeSize", 0),
                            "handler": fn.get("Handler", ""),
                            "last_modified": fn.get("LastModified", ""),
                        },
                        snapshot_time=datetime.now(UTC),
                    )
                )

        logger.info("Collected %d Lambda functions in %s", len(snapshots), region)
        return snapshots

    # -- VPN -------------------------------------------------------------------

    def _collect_vpn(self, region: str) -> list[ResourceSnapshot]:
        ec2 = self._session.client("ec2", region_name=region)
        snapshots: list[ResourceSnapshot] = []
        vpn_hourly = 0.05

        resp = ec2.describe_vpn_connections()
        for vpn in resp.get("VpnConnections", []):
            state = vpn.get("State", "unknown")
            tags = vpn.get("Tags", [])
            daily = round(vpn_hourly * _HOURS_PER_DAY, 4) if state == "available" else 0.0

            snapshots.append(
                ResourceSnapshot(
                    resource_id=vpn["VpnConnectionId"],
                    provider="aws",
                    account_id=self._account_id,
                    type="network",
                    service="VPN",
                    name=self._get_name_tag(tags),
                    region=region,
                    daily_cost=daily,
                    monthly_cost_estimate=round(daily * _DAYS_PER_MONTH, 4),
                    currency="USD",
                    state=state,
                    tags=self._flatten_tags(tags),
                    metadata={
                        "vpn_gateway_id": vpn.get("VpnGatewayId", ""),
                        "customer_gateway_id": vpn.get("CustomerGatewayId", ""),
                        "type": vpn.get("Type", ""),
                    },
                    snapshot_time=datetime.now(UTC),
                )
            )

        logger.info("Collected %d VPN connections in %s", len(snapshots), region)
        return snapshots

    # -- CloudFront (global) ---------------------------------------------------

    def _collect_cloudfront(self) -> list[ResourceSnapshot]:
        cf = self._session.client("cloudfront")
        snapshots: list[ResourceSnapshot] = []
        paginator = cf.get_paginator("list_distributions")

        for page in paginator.paginate():
            dist_list = page.get("DistributionList", {})
            for dist in dist_list.get("Items", []):
                snapshots.append(
                    ResourceSnapshot(
                        resource_id=dist["ARN"],
                        provider="aws",
                        account_id=self._account_id,
                        type="network",
                        service="CloudFront",
                        name=dist.get("DomainName", ""),
                        region="global",
                        daily_cost=0.0,  # usage-based
                        monthly_cost_estimate=0.0,
                        currency="USD",
                        state="active" if dist.get("Enabled") else "disabled",
                        tags={},
                        metadata={
                            "domain_name": dist.get("DomainName", ""),
                            "price_class": dist.get("PriceClass", ""),
                            "http_version": dist.get("HttpVersion", ""),
                            "origins": len(
                                dist.get("Origins", {}).get("Items", [])
                            ),
                        },
                        snapshot_time=datetime.now(UTC),
                    )
                )

        logger.info("Collected %d CloudFront distributions", len(snapshots))
        return snapshots

    # -- API Gateway -----------------------------------------------------------

    def _collect_api_gateway(self, region: str) -> list[ResourceSnapshot]:
        apigw = self._session.client("apigateway", region_name=region)
        snapshots: list[ResourceSnapshot] = []

        resp = apigw.get_rest_apis()
        for api in resp.get("items", []):
            snapshots.append(
                ResourceSnapshot(
                    resource_id=api["id"],
                    provider="aws",
                    account_id=self._account_id,
                    type="managed_service",
                    service="APIGateway",
                    name=api.get("name", ""),
                    region=region,
                    daily_cost=0.0,  # request-based
                    monthly_cost_estimate=0.0,
                    currency="USD",
                    state="active",
                    tags=api.get("tags", {}),
                    metadata={
                        "description": api.get("description", ""),
                        "endpoint_type": (
                            api.get("endpointConfiguration", {})
                            .get("types", [""])[0]
                        ),
                        "created_date": str(api.get("createdDate", "")),
                    },
                    snapshot_time=datetime.now(UTC),
                )
            )

        logger.info("Collected %d API Gateway APIs in %s", len(snapshots), region)
        return snapshots

    # -- CPU Metrics (CloudWatch) ----------------------------------------------

    def collect_cpu_metrics(self, snapshots: list[ResourceSnapshot]) -> list[ResourceSnapshot]:
        """Enrich running EC2 instances with average CPU utilization from CloudWatch."""
        now = datetime.now(UTC)
        start = now - timedelta(days=IDLE_CPU_LOOKBACK_DAYS)

        # Group EC2 instances by region
        by_region: dict[str, list[ResourceSnapshot]] = {}
        for s in snapshots:
            if s.service == "EC2" and s.state == "running" and s.provider == "aws":
                by_region.setdefault(s.region, []).append(s)

        for region, instances in by_region.items():
            try:
                cw = self._session.client("cloudwatch", region_name=region)
            except Exception:
                logger.warning("Could not create CloudWatch client for %s", region)
                continue

            for snap in instances:
                try:
                    resp = cw.get_metric_statistics(
                        Namespace="AWS/EC2",
                        MetricName="CPUUtilization",
                        Dimensions=[{"Name": "InstanceId", "Value": snap.resource_id}],
                        StartTime=start,
                        EndTime=now,
                        Period=86400,
                        Statistics=["Average"],
                    )
                    datapoints = resp.get("Datapoints", [])
                    if datapoints:
                        avg = sum(dp["Average"] for dp in datapoints) / len(datapoints)
                        snap.metadata["avg_cpu_percent"] = round(avg, 2)
                except Exception:
                    logger.debug(
                        "CloudWatch CPU lookup failed for %s", snap.resource_id, exc_info=True,
                    )

        return snapshots
