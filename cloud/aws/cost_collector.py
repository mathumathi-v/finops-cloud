# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import UTC, date, datetime
from typing import Any

import boto3

from cost_model.models import CostSnapshot

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
