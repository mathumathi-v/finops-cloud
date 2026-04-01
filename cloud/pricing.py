# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

"""Dynamic pricing providers with caching and fallback.

Each cloud provider implements :class:`PricingProvider` to fetch live
on-demand prices from the provider's pricing API.  :class:`CachedPricingProvider`
wraps any provider with an in-memory TTL cache.  All methods return ``None``
on failure so that callers can fall back to hardcoded pricing tables.
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class PricingProvider(ABC):
    """Base interface for cloud pricing lookups."""

    @abstractmethod
    def get_compute_hourly(self, instance_type: str, region: str) -> float | None:
        """Return on-demand hourly USD for a compute instance, or None."""

    @abstractmethod
    def get_storage_gb_month(self, storage_type: str, region: str) -> float | None:
        """Return per-GB per-month USD for a storage type, or None."""

    @abstractmethod
    def get_database_hourly(self, db_type: str, region: str) -> float | None:
        """Return on-demand hourly USD for a database instance, or None."""


class CachedPricingProvider(PricingProvider):
    """In-memory TTL cache wrapping another :class:`PricingProvider`."""

    def __init__(self, delegate: PricingProvider, ttl_seconds: int = 3600) -> None:
        self._delegate = delegate
        self._ttl = ttl_seconds
        self._cache: dict[tuple[str, ...], tuple[float, float | None]] = {}

    def _get(self, kind: str, key1: str, key2: str) -> float | None:
        cache_key = (kind, key1.lower(), key2.lower())
        entry = self._cache.get(cache_key)
        if entry and (time.time() - entry[0]) < self._ttl:
            return entry[1]

        fn = {
            "compute": self._delegate.get_compute_hourly,
            "storage": self._delegate.get_storage_gb_month,
            "database": self._delegate.get_database_hourly,
        }[kind]
        value = fn(key1, key2)
        self._cache[cache_key] = (time.time(), value)
        return value

    def get_compute_hourly(self, instance_type: str, region: str) -> float | None:
        return self._get("compute", instance_type, region)

    def get_storage_gb_month(self, storage_type: str, region: str) -> float | None:
        return self._get("storage", storage_type, region)

    def get_database_hourly(self, db_type: str, region: str) -> float | None:
        return self._get("database", db_type, region)


# ---------------------------------------------------------------------------
# AWS Pricing Provider
# ---------------------------------------------------------------------------


class AWSPricingProvider(PricingProvider):
    """Fetch live prices from the AWS Pricing API (us-east-1 only)."""

    def __init__(self, session: Any) -> None:
        self._pricing = session.client("pricing", region_name="us-east-1")

    def _get_product_price(self, filters: list[dict[str, str]]) -> float | None:
        try:
            resp = self._pricing.get_products(
                ServiceCode=filters[0]["Value"] if filters else "AmazonEC2",
                Filters=[
                    {"Type": "TERM_MATCH", "Field": f["Field"], "Value": f["Value"]}
                    for f in filters[1:]
                ],
                MaxResults=1,
            )
            import json

            for item in resp.get("PriceList", []):
                data = json.loads(item) if isinstance(item, str) else item
                terms = data.get("terms", {}).get("OnDemand", {})
                for term in terms.values():
                    for dim in term.get("priceDimensions", {}).values():
                        price = float(dim["pricePerUnit"].get("USD", "0"))
                        if price > 0:
                            return price
        except Exception:
            logger.debug("AWS Pricing API lookup failed", exc_info=True)
        return None

    @staticmethod
    def _region_to_location(region: str) -> str:
        """Map AWS region code to the human-readable location used by the Pricing API."""
        _MAP: dict[str, str] = {
            "us-east-1": "US East (N. Virginia)",
            "us-east-2": "US East (Ohio)",
            "us-west-1": "US West (N. California)",
            "us-west-2": "US West (Oregon)",
            "eu-west-1": "EU (Ireland)",
            "eu-west-2": "EU (London)",
            "eu-central-1": "EU (Frankfurt)",
            "ap-southeast-1": "Asia Pacific (Singapore)",
            "ap-southeast-2": "Asia Pacific (Sydney)",
            "ap-northeast-1": "Asia Pacific (Tokyo)",
        }
        return _MAP.get(region, region)

    def get_compute_hourly(self, instance_type: str, region: str) -> float | None:
        location = self._region_to_location(region)
        return self._get_product_price([
            {"Field": "ServiceCode", "Value": "AmazonEC2"},
            {"Field": "instanceType", "Value": instance_type},
            {"Field": "location", "Value": location},
            {"Field": "operatingSystem", "Value": "Linux"},
            {"Field": "tenancy", "Value": "Shared"},
            {"Field": "preInstalledSw", "Value": "NA"},
            {"Field": "capacitystatus", "Value": "Used"},
        ])

    def get_storage_gb_month(self, storage_type: str, region: str) -> float | None:
        location = self._region_to_location(region)
        return self._get_product_price([
            {"Field": "ServiceCode", "Value": "AmazonEC2"},
            {"Field": "productFamily", "Value": "Storage"},
            {"Field": "volumeApiName", "Value": storage_type},
            {"Field": "location", "Value": location},
        ])

    def get_database_hourly(self, db_type: str, region: str) -> float | None:
        location = self._region_to_location(region)
        return self._get_product_price([
            {"Field": "ServiceCode", "Value": "AmazonRDS"},
            {"Field": "instanceType", "Value": db_type},
            {"Field": "location", "Value": location},
            {"Field": "databaseEngine", "Value": "Any"},
        ])


# ---------------------------------------------------------------------------
# Azure Pricing Provider
# ---------------------------------------------------------------------------


class AzurePricingProvider(PricingProvider):
    """Fetch live prices from the Azure Retail Prices REST API (no auth required)."""

    _BASE_URL = "https://prices.azure.com/api/retail/prices"

    def _query(self, filter_str: str) -> float | None:
        try:
            import httpx

            resp = httpx.get(
                self._BASE_URL,
                params={"$filter": filter_str},
                timeout=10.0,
            )
            resp.raise_for_status()
            items = resp.json().get("Items", [])
            for item in items:
                price = item.get("unitPrice", 0)
                if price > 0:
                    return float(price)
        except Exception:
            logger.debug("Azure Pricing API lookup failed", exc_info=True)
        return None

    def get_compute_hourly(self, instance_type: str, region: str) -> float | None:
        return self._query(
            f"serviceName eq 'Virtual Machines' "
            f"and armSkuName eq '{instance_type}' "
            f"and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption' "
            f"and contains(meterName, 'Spot') eq false "
            f"and contains(meterName, 'Low Priority') eq false"
        )

    def get_storage_gb_month(self, storage_type: str, region: str) -> float | None:
        return self._query(
            f"serviceName eq 'Storage' "
            f"and armSkuName eq '{storage_type}' "
            f"and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        )

    def get_database_hourly(self, db_type: str, region: str) -> float | None:
        return self._query(
            f"serviceName eq 'SQL Database' "
            f"and armSkuName eq '{db_type}' "
            f"and armRegionName eq '{region}' "
            f"and priceType eq 'Consumption'"
        )


# ---------------------------------------------------------------------------
# GCP Pricing Provider
# ---------------------------------------------------------------------------


class GCPPricingProvider(PricingProvider):
    """Fetch live prices from the GCP Cloud Billing Catalog API."""

    def __init__(self, credentials: Any | None = None) -> None:
        self._credentials = credentials
        self._sku_cache: dict[str, list[Any]] | None = None

    def _get_skus(self) -> list[Any]:
        """Fetch all Compute Engine SKUs (cached per session)."""
        if self._sku_cache is not None:
            return self._sku_cache.get("compute", [])
        try:
            from google.cloud import billing_v1  # type: ignore[import-untyped]

            kwargs: dict[str, Any] = {}
            if self._credentials:
                kwargs["credentials"] = self._credentials
            client = billing_v1.CloudCatalogClient(**kwargs)

            # Find the Compute Engine service
            compute_service = None
            for svc in client.list_services():
                if "Compute Engine" in svc.display_name:
                    compute_service = svc.name
                    break

            if not compute_service:
                self._sku_cache = {"compute": []}
                return []

            skus = list(client.list_skus(parent=compute_service))
            self._sku_cache = {"compute": skus}
            return skus
        except Exception:
            logger.debug("GCP Billing Catalog lookup failed", exc_info=True)
            self._sku_cache = {"compute": []}
            return []

    @staticmethod
    def _nanos_to_usd(units: int, nanos: int) -> float:
        return float(units) + float(nanos) / 1e9

    def get_compute_hourly(self, instance_type: str, region: str) -> float | None:
        try:
            skus = self._get_skus()
            mt = instance_type.lower()
            for sku in skus:
                desc = (sku.description or "").lower()
                if mt not in desc:
                    continue
                for region_entry in sku.service_regions:
                    if region.lower() in region_entry.lower():
                        info = sku.pricing_info
                        if info:
                            expr = info[0].pricing_expression
                            if expr and expr.tiered_rates:
                                rate = expr.tiered_rates[0]
                                price = rate.unit_price
                                return self._nanos_to_usd(price.units, price.nanos)
        except Exception:
            logger.debug("GCP pricing lookup failed for %s", instance_type, exc_info=True)
        return None

    def get_storage_gb_month(self, storage_type: str, region: str) -> float | None:
        # Storage SKU matching is complex; return None to use hardcoded fallback
        return None

    def get_database_hourly(self, db_type: str, region: str) -> float | None:
        # Cloud SQL uses a separate service; return None to use hardcoded fallback
        return None
