# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import date
from typing import Any

from cloud.azure.cost_collector import AzureCostCollector
from cloud.azure.resource_collector import AzureResourceCollector
from cloud.base import CloudCollector
from cost_model.models import CostSnapshot, ResourceSnapshot

logger = logging.getLogger(__name__)


def _build_credential(
    tenant_id: str | None,
    client_id: str | None,
    client_secret: str | None,
) -> Any:
    """Return an appropriate azure-identity credential.

    - If all three of tenant_id, client_id, client_secret are provided,
      use ClientSecretCredential (service principal).
    - Otherwise fall back to DefaultAzureCredential which tries: environment
      variables, managed identity, Azure CLI, and interactive browser in order.
    """
    try:
        from azure.identity import (  # type: ignore[import-untyped]
            ClientSecretCredential,
            DefaultAzureCredential,
        )
    except ImportError as exc:
        raise ImportError(
            "azure-identity is required for Azure collection. "
            "Install it with: pip install azure-identity"
        ) from exc

    if tenant_id and client_id and client_secret:
        logger.info("Using Azure ClientSecretCredential (service principal)")
        return ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )

    logger.info("Using Azure DefaultAzureCredential (env / managed identity / az login)")
    return DefaultAzureCredential()


class AzureCollector(CloudCollector):
    """Unified Azure collector wrapping cost and resource sub-collectors."""

    def __init__(
        self,
        subscription_id: str,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        """
        Args:
            subscription_id: Azure subscription ID.
            tenant_id: Azure AD tenant ID (for service principal auth).
            client_id: Service principal application (client) ID.
            client_secret: Service principal client secret.
        """
        self._subscription_id = subscription_id
        credential = _build_credential(tenant_id, client_id, client_secret)

        self._cost_collector = AzureCostCollector(
            subscription_id=subscription_id,
            credential=credential,
        )
        self._resource_collector = AzureResourceCollector(
            subscription_id=subscription_id,
            credential=credential,
        )

    def collect_costs(self, start_date: date, end_date: date) -> list[CostSnapshot]:
        """Fetch cost data from Azure Cost Management API."""
        return self._cost_collector.collect_costs(start_date, end_date)

    def collect_resources(self) -> list[ResourceSnapshot]:
        """Fetch live Azure resource metadata."""
        return self._resource_collector.collect_resources()

    def test_connection(self) -> bool:
        """Verify Azure credentials by listing subscription details."""
        try:
            from azure.mgmt.resource import ResourceManagementClient  # type: ignore[import-untyped]

            credential = self._cost_collector._client._config.credential  # type: ignore[attr-defined]
            rm_client = ResourceManagementClient(credential, self._subscription_id)
            # A simple read-only call to verify credentials work
            next(iter(rm_client.resource_groups.list()), None)
            logger.info("Azure connection OK — subscription: %s", self._subscription_id)
            return True
        except Exception:
            logger.exception("Azure connection test failed")
            return False
