# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for enriching ResourceSnapshot metadata with CPU metrics.

All four provider collectors (AWS, GCP, Azure, OCI) call ``enrich_cpu_metadata``
after fetching daily CPU datapoints from their respective monitoring APIs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from cost_model.models import ResourceSnapshot


def enrich_cpu_metadata(
    snap: ResourceSnapshot,
    daily_values: list[dict[str, Any]],
) -> None:
    """Populate pattern-analysis metadata on *snap* from daily CPU datapoints.

    Args:
        snap: The resource snapshot to enrich (mutated in place).
        daily_values: A list of dicts, each with at least:
            - ``"date"``: ISO date string (YYYY-MM-DD)
            - ``"weekday"``: 0=Mon … 6=Sun
            - ``"avg"``: average CPU % for that day
            - ``"max"``: (optional) max CPU % for that day

    After this call, ``snap.metadata`` will contain:
        - ``avg_cpu_percent``: overall mean of daily averages
        - ``max_cpu_percent``: highest single-day max (or avg if max unavailable)
        - ``p95_cpu_percent``: 95th-percentile of daily averages
        - ``cpu_daily_values``: the raw daily_values list
        - ``cpu_trend_direction``: "declining", "increasing", or "stable"
        - ``cpu_weekday_avg``: mean CPU on Mon-Fri
        - ``cpu_weekend_avg``: mean CPU on Sat-Sun
    """
    if not daily_values:
        return

    avgs = [d["avg"] for d in daily_values]
    maxes = [d.get("max", d["avg"]) for d in daily_values]

    # Overall stats
    snap.metadata["avg_cpu_percent"] = round(sum(avgs) / len(avgs), 2)
    snap.metadata["max_cpu_percent"] = round(max(maxes), 2)
    snap.metadata["p95_cpu_percent"] = round(_percentile(avgs, 95), 2)
    snap.metadata["cpu_daily_values"] = daily_values

    # Weekday vs weekend split
    weekday_avgs = [d["avg"] for d in daily_values if d["weekday"] < 5]
    weekend_avgs = [d["avg"] for d in daily_values if d["weekday"] >= 5]
    snap.metadata["cpu_weekday_avg"] = (
        round(sum(weekday_avgs) / len(weekday_avgs), 2) if weekday_avgs else 0.0
    )
    snap.metadata["cpu_weekend_avg"] = (
        round(sum(weekend_avgs) / len(weekend_avgs), 2) if weekend_avgs else 0.0
    )

    # Trend: compare first half vs second half of the window
    snap.metadata["cpu_trend_direction"] = _compute_trend(avgs)


def enrich_from_cloudwatch_datapoints(
    snap: ResourceSnapshot,
    datapoints: list[dict[str, Any]],
) -> None:
    """Convert AWS CloudWatch-style datapoints and call ``enrich_cpu_metadata``.

    Each datapoint is expected to have ``Timestamp``, ``Average``, and
    optionally ``Maximum``.
    """
    sorted_dps = sorted(datapoints, key=lambda d: d["Timestamp"])
    daily_values = []
    for dp in sorted_dps:
        ts: datetime = dp["Timestamp"]
        daily_values.append({
            "date": ts.strftime("%Y-%m-%d"),
            "weekday": ts.weekday(),
            "avg": dp["Average"],
            "max": dp.get("Maximum", dp["Average"]),
        })
    enrich_cpu_metadata(snap, daily_values)


def enrich_from_daily_avg_list(
    snap: ResourceSnapshot,
    values: list[float],
    start_date: datetime,
) -> None:
    """Convert a flat list of daily average CPU values (oldest first) into
    enriched metadata.  Used by GCP, Azure, and OCI collectors that return
    ordered numeric lists rather than structured datapoints.
    """
    from datetime import timedelta

    daily_values = []
    for i, val in enumerate(values):
        day = start_date + timedelta(days=i)
        daily_values.append({
            "date": day.strftime("%Y-%m-%d"),
            "weekday": day.weekday(),
            "avg": val,
        })
    enrich_cpu_metadata(snap, daily_values)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _percentile(data: list[float], pct: float) -> float:
    """Compute the *pct*-th percentile of *data* using linear interpolation."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    n = len(sorted_data)
    if n == 1:
        return sorted_data[0]
    k = (pct / 100) * (n - 1)
    lo = int(k)
    hi = lo + 1
    if hi >= n:
        return sorted_data[-1]
    weight = k - lo
    return sorted_data[lo] * (1 - weight) + sorted_data[hi] * weight


def _compute_trend(values: list[float]) -> str:
    """Compare first-half average vs second-half average to determine trend."""
    if len(values) < 4:
        return "stable"
    mid = len(values) // 2
    first_half = sum(values[:mid]) / mid
    second_half = sum(values[mid:]) / (len(values) - mid)

    if first_half == 0:
        return "stable"

    change_pct = ((second_half - first_half) / first_half) * 100

    if change_pct < -20:
        return "declining"
    if change_pct > 20:
        return "increasing"
    return "stable"
