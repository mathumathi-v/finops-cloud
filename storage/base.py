# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod

from cost_model.models import AnomalyEvent, CostSnapshot, ResourceSnapshot, SavingsPlanSnapshot


class StorageAdapter(ABC):
    """Base interface for the persistence layer."""

    @abstractmethod
    def save_resource_snapshots(self, snapshots: list[ResourceSnapshot]) -> None:
        """Persist a batch of resource snapshots."""

    @abstractmethod
    def save_cost_snapshots(self, snapshots: list[CostSnapshot]) -> None:
        """Persist a batch of cost snapshots."""

    @abstractmethod
    def save_anomaly_events(self, events: list[AnomalyEvent]) -> None:
        """Persist detected anomaly events."""

    @abstractmethod
    def get_cost_history(self, provider: str, days: int) -> list[CostSnapshot]:
        """Return cost snapshots for the last N days."""

    @abstractmethod
    def save_savings_plan_snapshots(self, snapshots: list[SavingsPlanSnapshot]) -> None:
        """Persist a batch of savings plan snapshots."""

    @abstractmethod
    def get_savings_plan_snapshots(self, provider: str) -> list[SavingsPlanSnapshot]:
        """Return savings plan snapshots for a provider."""

    @abstractmethod
    def get_resource_snapshots(self, provider: str) -> list[ResourceSnapshot]:
        """Return the most recent resource snapshots for a provider."""
