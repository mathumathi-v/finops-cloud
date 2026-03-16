# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from collections import defaultdict
from datetime import UTC, datetime

from cost_model.models import AnomalyEvent, CostSnapshot, ResourceSnapshot
from intelligence.constants import (
    COST_SPIKE_MULTIPLIER,
    NEW_HIGH_COST_DAILY_THRESHOLD_USD,
    SUDDEN_SCALING_MULTIPLIER,
)

logger = logging.getLogger(__name__)


def detect_cost_spikes(cost_history: list[CostSnapshot]) -> list[AnomalyEvent]:
    """Detect services where current daily cost exceeds previous day by the spike multiplier."""
    events: list[AnomalyEvent] = []

    # Group costs by (service, region) and sort by period_start
    grouped: dict[tuple[str, str], list[CostSnapshot]] = defaultdict(list)
    for cs in cost_history:
        grouped[(cs.service, cs.region)].append(cs)

    for (service, region), snapshots in grouped.items():
        snapshots.sort(key=lambda s: s.period_start)
        for i in range(1, len(snapshots)):
            prev = snapshots[i - 1]
            curr = snapshots[i]

            if prev.cost_usd <= 0:
                continue

            if curr.cost_usd > prev.cost_usd * COST_SPIKE_MULTIPLIER:
                severity = _spike_severity(prev.cost_usd, curr.cost_usd)
                events.append(
                    AnomalyEvent(
                        provider=curr.provider,
                        account_id=curr.account_id,
                        resource_id=f"{service}/{region}",
                        anomaly_type="cost_spike",
                        severity=severity,
                        detail={
                            "service": service,
                            "region": region,
                            "previous_cost": prev.cost_usd,
                            "current_cost": curr.cost_usd,
                            "increase_pct": round(
                                ((curr.cost_usd - prev.cost_usd) / prev.cost_usd) * 100, 1
                            ),
                            "period": curr.period_start.isoformat(),
                        },
                        detected_at=datetime.now(UTC),
                    )
                )

    logger.info("Detected %d cost spike anomalies", len(events))
    return events


def detect_new_high_cost_resources(
    resources: list[ResourceSnapshot],
) -> list[AnomalyEvent]:
    """Flag new resources with daily cost exceeding the threshold."""
    events: list[AnomalyEvent] = []
    for r in resources:
        if r.daily_cost > NEW_HIGH_COST_DAILY_THRESHOLD_USD:
            events.append(
                AnomalyEvent(
                    provider=r.provider,
                    account_id=r.account_id,
                    resource_id=r.resource_id,
                    anomaly_type="new_high_cost",
                    severity="medium" if r.daily_cost < 200 else "high",
                    detail={
                        "service": r.service,
                        "name": r.name,
                        "daily_cost": r.daily_cost,
                        "region": r.region,
                    },
                    detected_at=datetime.now(UTC),
                )
            )

    logger.info("Detected %d new high-cost resource anomalies", len(events))
    return events


def detect_sudden_scaling(
    current_resources: list[ResourceSnapshot],
    previous_resources: list[ResourceSnapshot],
) -> list[AnomalyEvent]:
    """Detect services where instance count increased by more than the scaling multiplier."""
    events: list[AnomalyEvent] = []

    def count_by_service(resources: list[ResourceSnapshot]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for r in resources:
            if r.type == "compute":
                counts[f"{r.service}/{r.region}"] += 1
        return counts

    prev_counts = count_by_service(previous_resources)
    curr_counts = count_by_service(current_resources)

    for key, curr_count in curr_counts.items():
        prev_count = prev_counts.get(key, 0)
        if prev_count > 0 and curr_count > prev_count * SUDDEN_SCALING_MULTIPLIER:
            provider = current_resources[0].provider if current_resources else "unknown"
            account = current_resources[0].account_id if current_resources else "unknown"
            events.append(
                AnomalyEvent(
                    provider=provider,
                    account_id=account,
                    resource_id=key,
                    anomaly_type="sudden_scaling",
                    severity="high",
                    detail={
                        "previous_count": prev_count,
                        "current_count": curr_count,
                        "multiplier": round(curr_count / prev_count, 1),
                    },
                    detected_at=datetime.now(UTC),
                )
            )

    logger.info("Detected %d sudden scaling anomalies", len(events))
    return events


def _spike_severity(previous: float, current: float) -> str:
    increase_pct = ((current - previous) / previous) * 100
    if increase_pct >= 100:
        return "high"
    if increase_pct >= 50:
        return "medium"
    return "low"
