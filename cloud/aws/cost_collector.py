# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import UTC, date, datetime
from typing import Any

import boto3

from cost_model.models import CostSnapshot, SavingsPlanSnapshot

logger = logging.getLogger(__name__)


class AWSCostCollector:
    """Fetches aggregated cost data from AWS Cost Explorer."""

    def __init__(self, session: boto3.Session, account_id: str) -> None:
        self._ce = session.client("ce")
        self._account_id = account_id

    def collect_costs(self, start_date: date, end_date: date) -> list[CostSnapshot]:
        """Fetch cost data grouped by service and region for the given period."""
        snapshots: list[CostSnapshot] = []
        next_token: str | None = None

        while True:
            kwargs: dict[str, Any] = {
                "TimePeriod": {
                    "Start": start_date.isoformat(),
                    "End": end_date.isoformat(),
                },
                "Granularity": "DAILY",
                "Metrics": ["UnblendedCost"],
                "GroupBy": [
                    {"Type": "DIMENSION", "Key": "SERVICE"},
                    {"Type": "DIMENSION", "Key": "REGION"},
                ],
            }
            if next_token:
                kwargs["NextPageToken"] = next_token

            response = self._ce.get_cost_and_usage(**kwargs)  # type: ignore[arg-type]

            for result in response.get("ResultsByTime", []):
                period_start = date.fromisoformat(result["TimePeriod"]["Start"])
                period_end = date.fromisoformat(result["TimePeriod"]["End"])

                for group in result.get("Groups", []):
                    keys = group["Keys"]
                    service = keys[0] if len(keys) > 0 else "Unknown"
                    region = keys[1] if len(keys) > 1 else "global"
                    cost = float(group["Metrics"]["UnblendedCost"]["Amount"])

                    if cost == 0.0:
                        continue

                    snapshots.append(
                        CostSnapshot(
                            provider="aws",
                            account_id=self._account_id,
                            period_start=period_start,
                            period_end=period_end,
                            service=service,
                            region=region,
                            usage_type="",
                            cost_usd=cost,
                            snapshot_time=datetime.now(UTC),
                        )
                    )

            next_token = response.get("NextPageToken")
            if not next_token:
                break

        logger.info(
            "Collected %d cost snapshots from AWS for %s to %s",
            len(snapshots),
            start_date,
            end_date,
        )
        return snapshots

    def collect_savings_plans(self) -> list[SavingsPlanSnapshot]:
        """Fetch active Reserved Instances and Savings Plans."""
        snapshots: list[SavingsPlanSnapshot] = []

        # Reserved Instances
        try:
            ec2 = boto3.client("ec2")
            resp = ec2.describe_reserved_instances(
                Filters=[{"Name": "state", "Values": ["active", "payment-pending"]}]
            )
            for ri in resp.get("ReservedInstances", []):
                start = ri.get("Start")
                end = ri.get("End")
                snapshots.append(
                    SavingsPlanSnapshot(
                        provider="aws",
                        account_id=self._account_id,
                        plan_type="reserved_instance",
                        offering_id=ri.get("ReservedInstancesId", ""),
                        service="EC2",
                        region=ri.get("AvailabilityZone", ""),
                        start_date=start.date() if start else date.today(),
                        end_date=end.date() if end else date.today(),
                        commitment_usd_per_hour=float(ri.get("UsagePrice", 0)),
                        utilization_percent=0.0,
                        coverage_percent=0.0,
                        state=ri.get("State", "active"),
                        metadata={
                            "instance_type": ri.get("InstanceType", ""),
                            "instance_count": ri.get("InstanceCount", 0),
                            "offering_class": ri.get("OfferingClass", ""),
                            "offering_type": ri.get("OfferingType", ""),
                            "fixed_price": ri.get("FixedPrice", 0),
                        },
                    )
                )
        except Exception:
            logger.warning("Failed to collect Reserved Instances", exc_info=True)

        # Savings Plans utilization
        try:
            start_date = date.today().replace(day=1).isoformat()
            end_date = date.today().isoformat()
            resp = self._ce.get_savings_plans_utilization(
                TimePeriod={"Start": start_date, "End": end_date},
            )
            total = resp.get("Total", {})
            utilization = total.get("Utilization", {})
            snapshots.append(
                SavingsPlanSnapshot(
                    provider="aws",
                    account_id=self._account_id,
                    plan_type="savings_plan",
                    offering_id="aggregate",
                    service="all",
                    region="all",
                    start_date=date.fromisoformat(start_date),
                    end_date=date.fromisoformat(end_date),
                    commitment_usd_per_hour=float(
                        utilization.get("TotalCommitment", 0)
                    ),
                    utilization_percent=float(
                        utilization.get("UtilizationPercentage", 0)
                    ),
                    coverage_percent=0.0,
                    state="active",
                    metadata={
                        "used_commitment": utilization.get("UsedCommitment", "0"),
                        "unused_commitment": utilization.get("UnusedCommitment", "0"),
                        "net_savings": utilization.get("NetSavings", "0"),
                    },
                )
            )
        except Exception:
            logger.warning("Failed to collect Savings Plans utilization", exc_info=True)

        logger.info("Collected %d savings plan snapshots from AWS", len(snapshots))
        return snapshots
