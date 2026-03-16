# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field
from datetime import UTC, date, datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class ResourceSnapshot:
    """A point-in-time snapshot of a single cloud resource with cost data."""

    resource_id: str
    provider: str
    account_id: str
    type: str
    service: str
    name: str
    region: str
    daily_cost: float
    monthly_cost_estimate: float
    currency: str
    state: str
    tags: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)
    snapshot_time: datetime = field(default_factory=_utcnow)


@dataclass
class CostSnapshot:
    """Aggregated cost data for a service/region over a time period."""

    provider: str
    account_id: str
    period_start: date
    period_end: date
    service: str
    region: str
    usage_type: str
    cost_usd: float
    snapshot_time: datetime = field(default_factory=_utcnow)


@dataclass
class AnomalyEvent:
    """A detected cost anomaly."""

    provider: str
    account_id: str
    resource_id: str
    anomaly_type: str
    severity: str
    detail: dict[str, object] = field(default_factory=dict)
    detected_at: datetime = field(default_factory=_utcnow)
