# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import UTC, date, datetime

from cost_model.models import CostSnapshot

logger = logging.getLogger(__name__)

# BigQuery standard billing export column names
_BQ_QUERY = """
SELECT
    service.description         AS service,
    location.region             AS region,
    DATE(usage_start_time)      AS usage_date,
    SUM(cost)                   AS total_cost,
    MAX(currency)               AS currency
FROM `{table}`
WHERE
    DATE(usage_start_time) >= '{start_date}'
    AND DATE(usage_start_time) < '{end_date}'
    AND project.id = '{project_id}'
GROUP BY 1, 2, 3
HAVING total_cost > 0
ORDER BY usage_date, total_cost DESC
"""


class GCPCostCollector:
    """Reads GCP billing data from a BigQuery billing export table.

    The billing export must be enabled in the GCP Billing console and pointed
    at a BigQuery dataset before this collector can be used.
    """

    def __init__(
        self,
        project_id: str,
        billing_project_id: str,
        billing_dataset: str,
        billing_table: str,
        credentials: object | None = None,
    ) -> None:
        """
        Args:
            project_id: GCP project whose costs to query.
            billing_project_id: GCP project that hosts the BigQuery dataset.
            billing_dataset: BigQuery dataset name (e.g. ``my_billing_dataset``).
            billing_table: BigQuery table name (e.g.
                ``gcp_billing_export_v1_ABCDEF_123456_789012``).
            credentials: Optional google.oauth2 credentials object. If None,
                Application Default Credentials are used.
        """
        try:
            from google.cloud import bigquery  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "google-cloud-bigquery is required for GCP cost collection. "
                "Install it with: pip install google-cloud-bigquery"
            ) from exc

        self._project_id = project_id
        self._billing_project_id = billing_project_id
        self._billing_dataset = billing_dataset
        self._billing_table = billing_table
        self._bq = bigquery.Client(
            project=billing_project_id,
            credentials=credentials,  # type: ignore[arg-type]
        )

    @property
    def _full_table(self) -> str:
        return f"{self._billing_project_id}.{self._billing_dataset}.{self._billing_table}"

    def collect_costs(self, start_date: date, end_date: date) -> list[CostSnapshot]:
        """Fetch daily cost aggregates from BigQuery billing export."""
        query = _BQ_QUERY.format(
            table=self._full_table,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            project_id=self._project_id,
        )

        logger.info(
            "Querying GCP billing export for %s from %s to %s",
            self._project_id,
            start_date,
            end_date,
        )

        snapshots: list[CostSnapshot] = []
        for row in self._bq.query(query).result():
            service: str = row["service"] or "Unknown"
            region: str = row["region"] or "global"
            usage_date: date = row["usage_date"]
            cost: float = float(row["total_cost"])

            snapshots.append(
                CostSnapshot(
                    provider="gcp",
                    account_id=self._project_id,
                    period_start=usage_date,
                    period_end=usage_date,
                    service=service,
                    region=region,
                    usage_type="",
                    cost_usd=cost,
                    snapshot_time=datetime.now(UTC),
                )
            )

        logger.info(
            "Collected %d GCP cost snapshots for %s",
            len(snapshots),
            self._project_id,
        )
        return snapshots
