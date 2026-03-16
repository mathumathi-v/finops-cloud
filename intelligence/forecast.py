# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import calendar
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from cost_model.models import CostSnapshot
from intelligence.constants import FORECAST_LOOKBACK_DAYS

logger = logging.getLogger(__name__)


@dataclass
class ForecastResult:
    """Monthly cost projection and trend data."""

    provider: str
    account_id: str
    period: str
    avg_daily_cost: float
    projected_monthly_cost: float
    trend_slope: float
    trend_direction: str
    data_points: int


def compute_forecast(
    cost_history: list[CostSnapshot],
    target_month: date | None = None,
) -> list[ForecastResult]:
    """Compute monthly cost projection using linear regression over recent daily totals."""
    if not cost_history:
        return []

    target = target_month or date.today()
    days_in_month = calendar.monthrange(target.year, target.month)[1]

    # Group by (provider, account_id)
    grouped: dict[tuple[str, str], list[CostSnapshot]] = defaultdict(list)
    for cs in cost_history:
        grouped[(cs.provider, cs.account_id)].append(cs)

    results: list[ForecastResult] = []

    for (provider, account_id), snapshots in grouped.items():
        # Aggregate to daily totals
        daily_totals: dict[date, float] = defaultdict(float)
        for cs in snapshots:
            daily_totals[cs.period_start] += cs.cost_usd

        if not daily_totals:
            continue

        sorted_days = sorted(daily_totals.keys())
        # Use only last N days
        recent_days = sorted_days[-FORECAST_LOOKBACK_DAYS:]
        costs = [daily_totals[d] for d in recent_days]
        n = len(costs)

        avg_daily = sum(costs) / n
        projected = avg_daily * days_in_month

        # Simple linear regression: y = mx + b
        slope = _linear_slope(costs) if n >= 2 else 0.0

        if slope > 0.5:
            direction = "increasing"
        elif slope < -0.5:
            direction = "decreasing"
        else:
            direction = "stable"

        results.append(
            ForecastResult(
                provider=provider,
                account_id=account_id,
                period=target.strftime("%Y-%m"),
                avg_daily_cost=round(avg_daily, 2),
                projected_monthly_cost=round(projected, 2),
                trend_slope=round(slope, 4),
                trend_direction=direction,
                data_points=n,
            )
        )

    return results


def _linear_slope(values: list[float]) -> float:
    """Compute slope of simple linear regression over indexed values."""
    n = len(values)
    if n < 2:
        return 0.0

    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n

    numerator = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return 0.0

    return numerator / denominator
