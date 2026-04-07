# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import json
import logging
from collections import defaultdict
from typing import Any

from llm.sanitizer import redact_sensitive_data, sanitize_cloud_string

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a senior DevOps/FinOps engineer explaining cloud infrastructure costs. "
    "Write in plain text only — no markdown, no JSON, no bullet points with special characters. "
    "Be concise, specific, and actionable. Reference actual services and dollar amounts. "
    "If you see waste, explain what it is and what to do about it. "
    "If there are anomalies, explain the likely cause and recommended next steps."
)


def build_explain_prompt(context: dict[str, Any]) -> tuple[str, str]:
    """Build a prompt for the LLM to explain a cost context.

    Returns a (system_prompt, user_prompt) tuple.
    """
    redacted = _build_payload(context)

    user_prompt = (
        "Analyse the following cloud cost data and provide a clear explanation.\n\n"
        f"[DATA]\n{redacted}\n[/DATA]\n\n"
        "Explain the key cost drivers, any anomalies or waste, and specific recommendations "
        "to reduce spend. Write as plain text paragraphs."
    )

    return _SYSTEM_PROMPT, user_prompt


def build_spike_prompt(context: dict[str, Any]) -> tuple[str, str]:
    """Build a prompt specifically for explaining cost spikes."""
    redacted = _build_payload(context)

    user_prompt = (
        "The following cost anomalies were detected in cloud infrastructure.\n\n"
        f"[DATA]\n{redacted}\n[/DATA]\n\n"
        "For each anomaly, explain the likely cause and what the team should investigate. "
        "Prioritise by severity and dollar impact. Write as plain text paragraphs."
    )

    return _SYSTEM_PROMPT, user_prompt


def build_bill_prompt(context: dict[str, Any]) -> tuple[str, str]:
    """Build a prompt for full bill breakdown and reasoning."""
    redacted = _build_payload(context)

    user_prompt = (
        "Here is a complete cloud bill summary with top costs, anomalies, and waste findings.\n\n"
        f"[DATA]\n{redacted}\n[/DATA]\n\n"
        "Provide a comprehensive bill explanation covering:\n"
        "1. Where the money is going (top services and regions)\n"
        "2. What changed compared to the previous period\n"
        "3. What is being wasted and how much can be saved\n"
        "4. Specific actions to reduce the bill\n"
        "Write as plain text paragraphs."
    )

    return _SYSTEM_PROMPT, user_prompt


# ---------------------------------------------------------------------------
# Token budget configuration
# ---------------------------------------------------------------------------

# Maximum items per list section sent to the LLM
MAX_LIST_ITEMS: int = 10

# Target payload size in characters.  ~4 chars ≈ 1 token, so 4000 chars ≈
# 1000 tokens — leaves room for system/user prompt and response.
MAX_PAYLOAD_CHARS: int = 4000

# Metadata keys worth sending to the LLM (everything else is stripped)
_RESOURCE_KEYS = ("name", "service", "region", "state", "total_cost_usd", "percentage")
_WASTE_KEYS = ("waste_type", "service", "region", "name", "estimated_monthly_savings")
_ANOMALY_AGG_KEYS = ("service", "region", "severity", "occurrences", "avg_increase_pct", "latest_cost")


def _build_payload(context: dict[str, Any]) -> str:
    """Build a compact, token-efficient payload for LLM consumption.

    Applies four optimisations:
    1. **Field projection** — only sends fields the LLM needs (strips metadata,
       tags, cpu_daily_values, resource_id, etc.)
    2. **Anomaly aggregation** — groups duplicate anomalies by service/region
       into a single entry with occurrence count
    3. **Compact JSON** — no indentation, no unnecessary whitespace
    4. **Smart truncation** — trims lowest-priority sections first instead of
       cutting mid-JSON
    """
    compact = _compact_context(context)
    sanitized = _sanitize_context(compact)
    payload = json.dumps(sanitized, separators=(",", ":"), default=str)
    redacted = redact_sensitive_data(payload)

    if len(redacted) > MAX_PAYLOAD_CHARS:
        # Progressively drop sections by priority (waste > anomalies > resources > services)
        redacted = _smart_truncate(sanitized, MAX_PAYLOAD_CHARS)

    return redacted


def _compact_context(context: dict[str, Any]) -> dict[str, Any]:
    """Project only LLM-relevant fields and aggregate anomalies."""
    result: dict[str, Any] = {}

    # Pass-through scalars
    for key in ("provider", "period", "total_cost_usd"):
        if key in context:
            result[key] = context[key]

    # Top services — already compact, keep as-is
    if "top_services" in context:
        result["top_services"] = [
            {k: s[k] for k in ("name", "total_cost_usd", "percentage") if k in s}
            for s in context["top_services"][:MAX_LIST_ITEMS]
        ]

    # Top resources — strip metadata bloat
    if "top_resources" in context:
        result["top_resources"] = [
            {k: r[k] for k in _RESOURCE_KEYS if k in r}
            for r in context["top_resources"][:MAX_LIST_ITEMS]
        ]

    # Anomalies — aggregate by service+region instead of one entry per day
    if "anomalies" in context:
        result["anomalies"] = _aggregate_anomalies(context["anomalies"])

    # Waste — strip descriptions (LLM generates its own), keep only key facts
    if "waste" in context:
        result["waste"] = [
            {k: w[k] for k in _WASTE_KEYS if k in w}
            for w in context["waste"][:MAX_LIST_ITEMS]
        ]
        total_savings = sum(w.get("estimated_monthly_savings", 0) for w in context["waste"])
        result["waste_total_monthly_savings"] = round(total_savings, 2)
        result["waste_count"] = len(context["waste"])

    return result


def _aggregate_anomalies(anomalies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group anomalies by (service, region, severity) and summarise."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)

    for a in anomalies:
        detail = a.get("detail", {})
        key = (
            detail.get("service", a.get("resource_id", "unknown")),
            detail.get("region", ""),
            a.get("severity", "unknown"),
        )
        groups[key].append(a)

    result = []
    for (service, region, severity), items in groups.items():
        increases = []
        latest_cost = 0.0
        for item in items:
            detail = item.get("detail", {})
            if "increase_pct" in detail:
                increases.append(detail["increase_pct"])
            if "current_cost" in detail:
                latest_cost = max(latest_cost, detail["current_cost"])

        result.append({
            "service": service,
            "region": region,
            "severity": severity,
            "occurrences": len(items),
            "avg_increase_pct": round(sum(increases) / len(increases), 1) if increases else 0,
            "latest_cost": round(latest_cost, 2),
        })

    # Sort by latest_cost descending
    result.sort(key=lambda x: x["latest_cost"], reverse=True)
    return result[:MAX_LIST_ITEMS]


def _smart_truncate(context: dict[str, Any], budget: int) -> str:
    """Drop lowest-priority sections until payload fits within *budget* chars."""
    # Priority order: provider/period/total > services > resources > waste > anomalies
    section_priority = ["anomalies", "waste", "top_resources", "top_services"]

    working = dict(context)
    for section in section_priority:
        payload = json.dumps(working, separators=(",", ":"), default=str)
        redacted = redact_sensitive_data(payload)
        if len(redacted) <= budget:
            return redacted

        # Try halving the section first
        if section in working and isinstance(working[section], list):
            half = len(working[section]) // 2
            if half > 0:
                working[section] = working[section][:half]
                payload = json.dumps(working, separators=(",", ":"), default=str)
                redacted = redact_sensitive_data(payload)
                if len(redacted) <= budget:
                    return redacted

            # Still too big — drop entirely
            count = len(working.get(section, []))
            working.pop(section, None)
            working[f"{section}_omitted"] = f"{count} items omitted for token budget"

    # Last resort: hard truncate
    payload = json.dumps(working, separators=(",", ":"), default=str)
    redacted = redact_sensitive_data(payload)
    if len(redacted) > budget:
        redacted = redacted[:budget]
        logger.warning("LLM payload hard-truncated to %d chars", budget)
    return redacted


def _sanitize_context(context: dict[str, Any]) -> dict[str, Any]:
    """Deep-sanitize all string values in the context dict."""
    if isinstance(context, dict):
        return {k: _sanitize_context(v) for k, v in context.items()}
    if isinstance(context, list):
        return [_sanitize_context(item) for item in context]  # type: ignore[return-value]
    if isinstance(context, str):
        return sanitize_cloud_string(context)  # type: ignore[return-value]
    return context
