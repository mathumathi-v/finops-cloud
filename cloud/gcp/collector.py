# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import date
from pathlib import Path
from typing import Any

from cloud.base import CloudCollector
from cloud.gcp.cost_collector import GCPCostCollector
from cloud.gcp.resource_collector import GCPResourceCollector
from cost_model.models import CostSnapshot, ResourceSnapshot

logger = logging.getLogger(__name__)


def _load_credentials(credentials_file: str | None) -> Any | None:
    """Load a GCP service account credential from a JSON key file.

    Returns None to fall back to Application Default Credentials (ADC).
    """
    if not credentials_file:
        return None
    path = Path(credentials_file).expanduser()
    if not path.exists():
        logger.warning("GCP credentials file not found: %s — falling back to ADC", path)
        return None
    try:
        from google.oauth2 import service_account  # type: ignore[import-untyped]

        return service_account.Credentials.from_service_account_file(
            str(path),
            scopes=[
                "https://www.googleapis.com/auth/cloud-platform.read-only",
                "https://www.googleapis.com/auth/bigquery.readonly",
            ],
        )
    except Exception:
        logger.warning("Failed to load GCP credentials file — falling back to ADC", exc_info=True)
        return None


class GCPCollector(CloudCollector):
    """Unified GCP collector wrapping cost (BigQuery) and resource sub-collectors."""

    def __init__(
        self,
        project_id: str,
        credentials_file: str | None = None,
        billing_project_id: str | None = None,
        billing_dataset: str | None = None,
        billing_table: str | None = None,
    ) -> None:
        """
        Args:
            project_id: GCP project ID to collect resources and costs for.
            credentials_file: Path to a service account JSON key file.
                If None, Application Default Credentials are used.
            billing_project_id: Project that hosts the BigQuery billing export
                dataset. Defaults to ``project_id``.
            billing_dataset: BigQuery dataset for billing export.
            billing_table: BigQuery table for billing export.
        """
        self._project_id = project_id
        credentials = _load_credentials(credentials_file)

        self._resource_collector = GCPResourceCollector(
            project_id=project_id,
            credentials=credentials,
        )

        billing_proj = billing_project_id or project_id
        if billing_dataset and billing_table:
            self._cost_collector: GCPCostCollector | None = GCPCostCollector(
                project_id=project_id,
                billing_project_id=billing_proj,
                billing_dataset=billing_dataset,
                billing_table=billing_table,
                credentials=credentials,
            )
        else:
            self._cost_collector = None
            logger.warning(
                "GCP billing_dataset / billing_table not configured — "
                "cost collection disabled. Set these in config.yaml under gcp:"
            )

    def collect_costs(self, start_date: date, end_date: date) -> list[CostSnapshot]:
        """Fetch cost data from BigQuery billing export."""
        if self._cost_collector is None:
            logger.warning("GCP cost collection skipped — billing export not configured")
            return []
        return self._cost_collector.collect_costs(start_date, end_date)

    def collect_resources(self) -> list[ResourceSnapshot]:
        """Fetch live GCP resource metadata."""
        return self._resource_collector.collect_resources()

    def test_connection(self) -> bool:
        """Verify GCP credentials by listing Compute Engine zones."""
        try:
            from google.cloud import compute_v1  # type: ignore[import-untyped]

            kwargs: dict[str, Any] = {}
            client = compute_v1.ZonesClient(**kwargs)
            request = compute_v1.ListZonesRequest(project=self._project_id, max_results=1)
            next(iter(client.list(request=request)), None)
            return True
        except Exception:
            logger.exception("GCP connection test failed")
            return False
