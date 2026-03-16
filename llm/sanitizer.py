# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import re

# Patterns that look like prompt injection attempts
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+previous", re.IGNORECASE),
    re.compile(r"you\s+are\s+now", re.IGNORECASE),
    re.compile(r"system\s*:", re.IGNORECASE),
    re.compile(r"<\s*system\s*>", re.IGNORECASE),
    re.compile(r"forget\s+(all|your|everything)", re.IGNORECASE),
    re.compile(r"new\s+instructions?:", re.IGNORECASE),
]

# Max length for tag values or cloud-sourced strings
MAX_TAG_VALUE_LENGTH: int = 200

# Patterns to redact from prompt data
_ACCOUNT_ID_PATTERN = re.compile(r"\b\d{12}\b")
_ARN_PATTERN = re.compile(r"arn:aws[a-zA-Z-]*:[a-zA-Z0-9-]+:\S+")
_IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_HOSTNAME_PATTERN = re.compile(
    r"\b(?:ip-\d+-\d+-\d+-\d+|ec2-\d+-\d+-\d+-\d+)\.[a-z0-9.-]+\b"
)


def sanitize_cloud_string(value: str) -> str:
    """Sanitize a cloud-sourced string before inserting it into an LLM prompt."""
    truncated = value[:MAX_TAG_VALUE_LENGTH]

    for pattern in _INJECTION_PATTERNS:
        truncated = pattern.sub("[REDACTED]", truncated)

    return truncated


def redact_sensitive_data(text: str) -> str:
    """Redact account IDs, ARNs, IPs, and internal hostnames from prompt text."""
    text = _ARN_PATTERN.sub("[ARN_REDACTED]", text)
    text = _HOSTNAME_PATTERN.sub("[HOSTNAME_REDACTED]", text)
    text = _ACCOUNT_ID_PATTERN.sub("[ACCOUNT_REDACTED]", text)
    text = _IP_PATTERN.sub("[IP_REDACTED]", text)
    return text
