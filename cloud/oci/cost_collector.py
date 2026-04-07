# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import date, datetime, timezone

from cost_model.models import CostSnapshot

logger = logging.getLogger(__name__)


class OCICostCollector:
    """Fetches aggregated cost data from the OCI Usage API.

    Requires the ``usage-report`` policy on the tenancy or compartment.
    Uses the UsageapiClient to query cost and usage reports.
    """

    def __init__(self, tenancy_id: str, config: object) -> None:
        """
        Args:
            tenancy_id: OCI tenancy OCID.
            config: An oci.config dict or compatible config object.
        """
        try:
            import oci  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "oci SDK is required for OCI cost collection. "
                "Install it with: pip install oci"
            ) from exc

        self._tenancy_id = tenancy_id
        self._client = oci.usage_api.UsageapiClient(config)

    def collect_costs(self, start_date: date, end_date: date) -> list[CostSnapshot]:
        """Fetch daily cost data grouped by service and region."""
        import oci  # type: ignore[import-untyped]

        time_start = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        time_end = datetime.combine(end_date, datetime.min.time()).replace(tzinfo=timezone.utc)

        request = oci.usage_api.models.RequestSummarizedUsagesDetails(
            tenant_id=self._tenancy_id,
            time_usage_started=time_start.isoformat(),
            time_usage_ended=time_end.isoformat(),
            granularity="DAILY",
            query_type="COST",
            group_by=["service", "region"],
        )

        logger.info(
            "Querying OCI Usage API for %s from %s to %s",
            self._tenancy_id,
            start_date,
            end_date,
        )

        result = self._client.request_summarized_usages(request)
        snapshots: list[CostSnapshot] = []

        for item in result.data.items or []:
            cost = float(item.computed_amount or 0.0)
            if cost == 0.0:
                continue

            service = item.service or "Unknown"
            region = item.region or "global"

            # Parse the usage date
            usage_start = item.time_usage_started
            if isinstance(usage_start, str):
                try:
                    usage_date = date.fromisoformat(usage_start[:10])
                except (ValueError, IndexError):
                    logger.warning("Could not parse usage date: %s — skipping row", usage_start)
                    continue
            elif hasattr(usage_start, "date"):
                usage_date = usage_start.date()
            else:
                continue

            snapshots.append(
                CostSnapshot(
                    provider="oci",
                    account_id=self._tenancy_id,
                    period_start=usage_date,
                    period_end=usage_date,
                    service=service,
                    region=region.lower().replace(" ", "-"),
                    usage_type="",
                    cost_usd=cost,
                    snapshot_time=datetime.now(timezone.utc),
                )
            )

        logger.info(
            "Collected %d OCI cost snapshots for tenancy %s",
            len(snapshots),
            self._tenancy_id,
        )
        return snapshots
