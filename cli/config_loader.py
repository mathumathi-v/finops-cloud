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

    merged = _deep_merge(_default_config(), config)
    return _normalize_provider_accounts(merged)


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


def _normalize_provider_accounts(config: dict[str, Any]) -> dict[str, Any]:
    """Normalise provider config so both old single-account and new multi-account
    formats are stored as a list under ``accounts``.

    Old (still supported)::

        aws:
          enabled: true
          profile: default
          regions: ["us-east-1"]

    New::

        aws:
          enabled: true
          accounts:
            - name: production
              profile: default
              regions: ["us-east-1"]
    """
    _PROVIDER_KEYS = ("aws", "gcp", "azure", "oci")
    # Keys that live at the provider level, not per-account
    _META_KEYS = {"enabled", "accounts"}

    for provider in _PROVIDER_KEYS:
        section = config.get(provider)
        if not isinstance(section, dict):
            continue

        # Already has accounts list — nothing to migrate
        if "accounts" in section and isinstance(section["accounts"], list):
            continue

        # Collect per-account keys from the flat layout
        acct: dict[str, Any] = {}
        for k, v in list(section.items()):
            if k not in _META_KEYS:
                acct[k] = v

        if acct:
            acct.setdefault("name", "default")
            section["accounts"] = [acct]
            # Remove migrated keys from the provider level
            for k in acct:
                if k != "name":
                    section.pop(k, None)

    return config


def _default_config() -> dict[str, Any]:
    return {
        "aws": {
            "enabled": True,
            "accounts": [
                {
                    "name": "default",
                    "profile": "default",
                    "access_key_id": "",
                    "secret_access_key": "",
                    "regions": ["us-east-1"],
                },
            ],
        },
        "gcp": {"enabled": False, "accounts": []},
        "azure": {"enabled": False, "accounts": []},
        "oci": {"enabled": False, "accounts": []},
        "llm": {
            "provider": "openai",
            "api_key": "",
            "model": "gpt-4o",
            "base_url": "",
            "bedrock_region": "us-east-1",
        },
        "storage": {"path": "~/.finops-agent/finops.db"},
        "scheduler": {"enabled": False, "interval_hours": 24},
    }
