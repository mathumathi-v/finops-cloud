# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
import os
import stat
import sys
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATHS: list[str] = [
    os.path.expanduser("~/.finops-agent/config.yaml"),
    "config.yaml",
]


def load_config(path: str | None = None) -> dict[str, Any]:
    """Load and validate config from YAML file.

    User home config (~/.finops-agent/config.yaml) takes priority over local.
    Checks file permissions and refuses to run if the file is world-readable
    and contains credentials.
    """
    config_path = _resolve_path(path)
    if config_path is None:
        logger.warning("No config file found. Using defaults.")
        return _default_config()

    _check_permissions(config_path)

    with open(config_path) as f:
        config: dict[str, Any] = yaml.safe_load(f) or {}

    return _deep_merge(_default_config(), config)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base, preferring override values."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _resolve_path(path: str | None) -> Path | None:
    if path:
        p = Path(path)
        if p.exists():
            return p
        logger.error("Config file not found: %s", path)
        sys.exit(1)

    for candidate in DEFAULT_CONFIG_PATHS:
        p = Path(candidate)
        if p.exists():
            return p

    return None


def _check_permissions(path: Path) -> None:
    """Warn if config file is readable by group or others."""
    mode = path.stat().st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        # Check if it actually contains credentials
        with open(path) as f:
            content = f.read()

        sensitive_keys = ["access_key_id", "secret_access_key", "api_key", "client_secret"]
        def _has_value(key: str) -> bool:
            parts = content.split(key)
            return len(parts) > 1 and bool(
                parts[1].strip().lstrip(":").strip().strip('"').strip("'")
            )

        has_creds = any(_has_value(k) for k in sensitive_keys)

        if has_creds:
            logger.error(
                "Config file %s has insecure permissions (readable by group/others) "
                "and contains credentials. Run: chmod 600 %s",
                path,
                path,
            )
            sys.exit(1)


def _default_config() -> dict[str, Any]:
    return {
        "aws": {
            "enabled": True,
            "profile": "default",
            "access_key_id": "",
            "secret_access_key": "",
            "regions": ["us-east-1"],
        },
        "gcp": {"enabled": False},
        "azure": {"enabled": False},
        "llm": {
            "provider": "openai",
            "api_key": "",
            "model": "gpt-4o",
            "base_url": "",
        },
        "storage": {"path": "~/.finops-agent/finops.db"},
        "scheduler": {"enabled": False, "interval_hours": 24},
    }
