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
    resource_id: str = ""
    service: str = ""
    region: str = ""
    state: str = ""


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
    # Build list with full resource details, then sort and rank.
    entries: list[tuple[ResourceSnapshot, float]] = [
        (r, r.daily_cost) for r in resources if r.daily_cost > 0
    ]
    entries.sort(key=lambda x: x[1], reverse=True)
    entries = entries[:n]

    grand_total = sum(cost for _, cost in entries)
    if grand_total == 0:
        return []

    return [
        CostContributor(
            name=r.name or r.resource_id,
            total_cost_usd=round(cost, 2),
            percentage=round((cost / grand_total) * 100, 1),
            resource_id=r.resource_id,
            service=r.service,
            region=r.region,
            state=r.state,
        )
        for r, cost in entries
    ]


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
