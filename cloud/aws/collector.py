# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import date

import boto3

from cloud.aws.cost_collector import AWSCostCollector
from cloud.aws.resource_collector import AWSResourceCollector
from cloud.base import CloudCollector
from cost_model.models import CostSnapshot, ResourceSnapshot

logger = logging.getLogger(__name__)


class AWSCollector(CloudCollector):
    """Unified AWS collector wrapping cost and resource sub-collectors."""

    def __init__(
        self,
        profile: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        regions: list[str] | None = None,
    ) -> None:
        session_kwargs: dict[str, str] = {}
        if profile:
            session_kwargs["profile_name"] = profile
        if access_key_id and secret_access_key:
            session_kwargs["aws_access_key_id"] = access_key_id
            session_kwargs["aws_secret_access_key"] = secret_access_key

        self._session = boto3.Session(**session_kwargs)  # type: ignore[arg-type]
        self._regions = regions or ["us-east-1"]

        sts = self._session.client("sts")
        self._account_id = sts.get_caller_identity()["Account"]

        self._cost_collector = AWSCostCollector(self._session, self._account_id)
        self._resource_collector = AWSResourceCollector(
            self._session, self._account_id, self._regions
        )

    def collect_costs(self, start_date: date, end_date: date) -> list[CostSnapshot]:
        """Fetch aggregated cost data for the period."""
        return self._cost_collector.collect_costs(start_date, end_date)

    def collect_resources(self) -> list[ResourceSnapshot]:
        """Fetch current live resource metadata and cost."""
        return self._resource_collector.collect_resources()

    def test_connection(self) -> bool:
        """Verify credentials work by calling STS."""
        try:
            sts = self._session.client("sts")
            sts.get_caller_identity()
            return True
        except Exception:
            logger.exception("AWS connection test failed")
            return False
