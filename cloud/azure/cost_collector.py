# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import UTC, date, datetime

from cost_model.models import CostSnapshot

logger = logging.getLogger(__name__)

# Azure Cost Management query granularity and grouping
_QUERY_BODY = {
    "type": "ActualCost",
    "timeframe": "Custom",
    "granularity": "Daily",
    "dataset": {
        "aggregation": {
            "totalCost": {"name": "Cost", "function": "Sum"}
        },
        "grouping": [
            {"type": "Dimension", "name": "ServiceName"},
            {"type": "Dimension", "name": "ResourceLocation"},
        ],
    },
}


class AzureCostCollector:
    """Fetches aggregated cost data from Azure Cost Management API.

    Requires the ``Cost Management Reader`` role on the subscription.
    """

    def __init__(self, subscription_id: str, credential: object) -> None:
        """
        Args:
            subscription_id: Azure subscription ID.
            credential: An azure-identity credential object
                (e.g. ``ClientSecretCredential``, ``DefaultAzureCredential``).
        """
        try:
            from azure.mgmt.costmanagement import (
                CostManagementClient,  # type: ignore[import-untyped]
            )
        except ImportError as exc:
            raise ImportError(
                "azure-mgmt-costmanagement is required for Azure cost collection. "
                "Install it with: pip install azure-mgmt-costmanagement"
            ) from exc

        self._subscription_id = subscription_id
        self._scope = f"/subscriptions/{subscription_id}"
        self._client = CostManagementClient(credential)  # type: ignore[arg-type]

    def collect_costs(self, start_date: date, end_date: date) -> list[CostSnapshot]:
        """Fetch daily cost data grouped by service and region."""
        from azure.mgmt.costmanagement.models import (  # type: ignore[import-untyped]
            QueryAggregation,
            QueryDataset,
            QueryDefinition,
            QueryGrouping,
            QueryTimePeriod,
        )

        query = QueryDefinition(
            type="ActualCost",
            timeframe="Custom",
            time_period=QueryTimePeriod(
                from_property=datetime.combine(start_date, datetime.min.time()),
                to=datetime.combine(end_date, datetime.min.time()),
            ),
            dataset=QueryDataset(
                granularity="Daily",
                aggregation={
                    "totalCost": QueryAggregation(name="Cost", function="Sum")
                },
                grouping=[
                    QueryGrouping(type="Dimension", name="ServiceName"),
                    QueryGrouping(type="Dimension", name="ResourceLocation"),
                ],
            ),
        )

        logger.info(
            "Querying Azure Cost Management for %s from %s to %s",
            self._subscription_id,
            start_date,
            end_date,
        )

        result = self._client.query.usage(scope=self._scope, parameters=query)

        # Result columns: [Cost, UsageDate, ServiceName, ResourceLocation, Currency]
        columns = [col.name for col in (result.columns or [])]  # type: ignore[union-attr]
        snapshots: list[CostSnapshot] = []

        for row in result.rows or []:  # type: ignore[union-attr]
            row_dict = dict(zip(columns, row, strict=False))
            cost = float(row_dict.get("Cost", 0.0))
            if cost == 0.0:
                continue

            usage_date_raw = str(row_dict.get("UsageDate", ""))
            # UsageDate is returned as int YYYYMMDD or string
            try:
                usage_date = date(
                    int(usage_date_raw[:4]),
                    int(usage_date_raw[4:6]),
                    int(usage_date_raw[6:8]),
                )
            except (ValueError, IndexError):
                logger.warning("Could not parse UsageDate: %s — skipping row", usage_date_raw)
                continue

            service = str(row_dict.get("ServiceName", "Unknown") or "Unknown")
            region = str(row_dict.get("ResourceLocation", "global") or "global")

            snapshots.append(
                CostSnapshot(
                    provider="azure",
                    account_id=self._subscription_id,
                    period_start=usage_date,
                    period_end=usage_date,
                    service=service,
                    region=region.lower().replace(" ", "-"),
                    usage_type="",
                    cost_usd=cost,
                    snapshot_time=datetime.now(UTC),
                )
            )

        logger.info(
            "Collected %d Azure cost snapshots for subscription %s",
            len(snapshots),
            self._subscription_id,
        )
        return snapshots
