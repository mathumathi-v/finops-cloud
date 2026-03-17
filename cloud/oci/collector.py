# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import date
from typing import Any

from cloud.base import CloudCollector
from cloud.oci.cost_collector import OCICostCollector
from cloud.oci.resource_collector import OCIResourceCollector
from cost_model.models import CostSnapshot, ResourceSnapshot

logger = logging.getLogger(__name__)


def _build_config(
    config_file: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Return an OCI SDK config dict.

    - If *config_file* is provided, load from that file with the given profile.
    - Otherwise fall back to ``~/.oci/config`` with DEFAULT profile.
    """
    try:
        import oci  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "oci SDK is required for OCI collection. "
            "Install it with: pip install oci"
        ) from exc

    profile_name = profile or "DEFAULT"
    if config_file:
        logger.info("Loading OCI config from %s (profile: %s)", config_file, profile_name)
        return oci.config.from_file(file_location=config_file, profile_name=profile_name)

    logger.info("Loading OCI config from default location (profile: %s)", profile_name)
    return oci.config.from_file(profile_name=profile_name)


class OCICollector(CloudCollector):
    """Unified OCI collector wrapping cost and resource sub-collectors."""

    def __init__(
        self,
        compartment_id: str,
        tenancy_id: str | None = None,
        config_file: str | None = None,
        profile: str | None = None,
    ) -> None:
        """
        Args:
            compartment_id: OCI compartment OCID to scan for resources.
            tenancy_id: OCI tenancy OCID (for cost queries). Defaults to
                the tenancy in the OCI config.
            config_file: Path to OCI config file. Defaults to ``~/.oci/config``.
            profile: Profile name within the OCI config file.
        """
        self._compartment_id = compartment_id
        oci_config = _build_config(config_file, profile)

        resolved_tenancy = tenancy_id or oci_config.get("tenancy", "")
        self._tenancy_id = resolved_tenancy
        self._oci_config = oci_config

        self._cost_collector = OCICostCollector(
            tenancy_id=resolved_tenancy,
            config=oci_config,
        )
        self._resource_collector = OCIResourceCollector(
            compartment_id=compartment_id,
            config=oci_config,
        )

    def collect_costs(self, start_date: date, end_date: date) -> list[CostSnapshot]:
        """Fetch cost data from OCI Usage API."""
        return self._cost_collector.collect_costs(start_date, end_date)

    def collect_resources(self) -> list[ResourceSnapshot]:
        """Fetch live OCI resource metadata."""
        return self._resource_collector.collect_resources()

    def test_connection(self) -> bool:
        """Verify OCI credentials by listing compartments."""
        try:
            import oci  # type: ignore[import-untyped]

            identity_client = oci.identity.IdentityClient(self._oci_config)
            identity_client.get_tenancy(self._tenancy_id)
            logger.info("OCI connection OK — tenancy: %s", self._tenancy_id)
            return True
        except Exception:
            logger.exception("OCI connection test failed")
            return False
