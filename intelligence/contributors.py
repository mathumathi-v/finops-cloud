# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from collections import defaultdict
from dataclasses import dataclass

from cost_model.models import CostSnapshot, ResourceSnapshot
from intelligence.constants import TOP_N_RESULTS

logger = logging.getLogger(__name__)


@dataclass
class CostContributor:
    """A ranked cost contributor (region, service, or resource)."""

    name: str
    total_cost_usd: float
    percentage: float


def top_regions(cost_history: list[CostSnapshot], n: int = TOP_N_RESULTS) -> list[CostContributor]:
    """Return top N regions by total cost."""
    totals: dict[str, float] = defaultdict(float)
    for cs in cost_history:
        totals[cs.region] += cs.cost_usd

    return _rank(totals, n)


def top_services(
    cost_history: list[CostSnapshot], n: int = TOP_N_RESULTS
) -> list[CostContributor]:
    """Return top N services by total cost."""
    totals: dict[str, float] = defaultdict(float)
    for cs in cost_history:
        totals[cs.service] += cs.cost_usd

    return _rank(totals, n)


def top_resources(
    resources: list[ResourceSnapshot], n: int = TOP_N_RESULTS
) -> list[CostContributor]:
    """Return top N resources by daily cost."""
    totals: dict[str, float] = {}
    for r in resources:
        label = f"{r.service}/{r.name or r.resource_id}"
        totals[label] = r.daily_cost

    return _rank(totals, n)


def _rank(totals: dict[str, float], n: int) -> list[CostContributor]:
    grand_total = sum(totals.values())
    if grand_total == 0:
        return []

    sorted_items = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:n]
    return [
        CostContributor(
            name=name,
            total_cost_usd=round(cost, 2),
            percentage=round((cost / grand_total) * 100, 1),
        )
        for name, cost in sorted_items
    ]
