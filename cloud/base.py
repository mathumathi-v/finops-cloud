# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from datetime import date

from cost_model.models import CostSnapshot, ResourceSnapshot


class CloudCollector(ABC):
    """Base interface for all cloud provider collectors."""

    @abstractmethod
    def collect_costs(self, start_date: date, end_date: date) -> list[CostSnapshot]:
        """Fetch aggregated cost data for the period."""

    @abstractmethod
    def collect_resources(self) -> list[ResourceSnapshot]:
        """Fetch current live resource metadata and cost."""

    @abstractmethod
    def test_connection(self) -> bool:
        """Verify credentials work."""
