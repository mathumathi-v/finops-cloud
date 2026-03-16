# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import json
import logging
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


# Groq free tier / most hosted models have ~6000 token input limits.
# We cap list items and total payload to stay well within limits.
MAX_LIST_ITEMS: int = 10
MAX_PAYLOAD_CHARS: int = 8000


def _build_payload(context: dict[str, Any]) -> str:
    """Sanitize, truncate, and serialise context to a string safe for LLM sending."""
    sanitized = _sanitize_context(context)
    payload = json.dumps(sanitized, indent=2, default=str)
    redacted = redact_sensitive_data(payload)
    if len(redacted) > MAX_PAYLOAD_CHARS:
        redacted = redacted[:MAX_PAYLOAD_CHARS] + "\n... (truncated for length)"
        logger.warning("LLM payload truncated to %d chars", MAX_PAYLOAD_CHARS)
    return redacted


def _sanitize_context(context: dict[str, Any]) -> dict[str, Any]:
    """Deep-sanitize all string values in the context dict."""
    if isinstance(context, dict):
        return {k: _sanitize_context(v) for k, v in context.items()}
    if isinstance(context, list):
        truncated = context[:MAX_LIST_ITEMS]
        result = [_sanitize_context(item) for item in truncated]
        if len(context) > MAX_LIST_ITEMS:
            result.append(f"... and {len(context) - MAX_LIST_ITEMS} more items")
        return result  # type: ignore[return-value]
    if isinstance(context, str):
        return sanitize_cloud_string(context)  # type: ignore[return-value]
    return context
