# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for dynamic pricing providers and caching."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from cloud.pricing import (
    AWSPricingProvider,
    AzurePricingProvider,
    CachedPricingProvider,
    PricingProvider,
)


# ---------------------------------------------------------------------------
# CachedPricingProvider
# ---------------------------------------------------------------------------


class _StubProvider(PricingProvider):
    """Stub that tracks call count."""

    def __init__(self, value: float | None = 0.10) -> None:
        self.value = value
        self.call_count = 0

    def get_compute_hourly(self, instance_type: str, region: str) -> float | None:
        self.call_count += 1
        return self.value

    def get_storage_gb_month(self, storage_type: str, region: str) -> float | None:
        self.call_count += 1
        return self.value

    def get_database_hourly(self, db_type: str, region: str) -> float | None:
        self.call_count += 1
        return self.value


class TestCachedPricingProvider:
    def test_cache_hit_avoids_delegate(self) -> None:
        stub = _StubProvider(0.50)
        cached = CachedPricingProvider(stub, ttl_seconds=3600)

        result1 = cached.get_compute_hourly("t3.medium", "us-east-1")
        result2 = cached.get_compute_hourly("t3.medium", "us-east-1")

        assert result1 == 0.50
        assert result2 == 0.50
        assert stub.call_count == 1  # only called once

    def test_different_keys_call_delegate(self) -> None:
        stub = _StubProvider(0.25)
        cached = CachedPricingProvider(stub, ttl_seconds=3600)

        cached.get_compute_hourly("t3.medium", "us-east-1")
        cached.get_compute_hourly("m5.large", "us-east-1")

        assert stub.call_count == 2

    def test_none_values_are_cached(self) -> None:
        stub = _StubProvider(None)
        cached = CachedPricingProvider(stub, ttl_seconds=3600)

        result1 = cached.get_compute_hourly("unknown", "us-east-1")
        result2 = cached.get_compute_hourly("unknown", "us-east-1")

        assert result1 is None
        assert result2 is None
        assert stub.call_count == 1

    def test_expired_cache_calls_delegate_again(self) -> None:
        stub = _StubProvider(0.10)
        cached = CachedPricingProvider(stub, ttl_seconds=0)  # immediate expiry

        cached.get_compute_hourly("t3.medium", "us-east-1")
        time.sleep(0.01)
        cached.get_compute_hourly("t3.medium", "us-east-1")

        assert stub.call_count == 2

    def test_storage_and_database_methods(self) -> None:
        stub = _StubProvider(0.08)
        cached = CachedPricingProvider(stub, ttl_seconds=3600)

        assert cached.get_storage_gb_month("gp3", "us-east-1") == 0.08
        assert cached.get_database_hourly("db.t3.medium", "us-east-1") == 0.08


# ---------------------------------------------------------------------------
# AWSPricingProvider
# ---------------------------------------------------------------------------


class TestAWSPricingProvider:
    def test_parses_product_price(self) -> None:
        mock_session = MagicMock()
        mock_pricing = MagicMock()
        mock_session.client.return_value = mock_pricing

        price_item = {
            "terms": {
                "OnDemand": {
                    "term1": {
                        "priceDimensions": {
                            "dim1": {
                                "pricePerUnit": {"USD": "0.0416"},
                            }
                        }
                    }
                }
            }
        }
        mock_pricing.get_products.return_value = {
            "PriceList": [json.dumps(price_item)]
        }

        provider = AWSPricingProvider(mock_session)
        result = provider.get_compute_hourly("t3.medium", "us-east-1")

        assert result == pytest.approx(0.0416)

    def test_returns_none_on_error(self) -> None:
        mock_session = MagicMock()
        mock_pricing = MagicMock()
        mock_session.client.return_value = mock_pricing
        mock_pricing.get_products.side_effect = Exception("access denied")

        provider = AWSPricingProvider(mock_session)
        result = provider.get_compute_hourly("t3.medium", "us-east-1")

        assert result is None

    def test_returns_none_on_empty_pricelist(self) -> None:
        mock_session = MagicMock()
        mock_pricing = MagicMock()
        mock_session.client.return_value = mock_pricing
        mock_pricing.get_products.return_value = {"PriceList": []}

        provider = AWSPricingProvider(mock_session)
        assert provider.get_compute_hourly("unknown", "us-east-1") is None

    def test_region_to_location_mapping(self) -> None:
        assert AWSPricingProvider._region_to_location("us-east-1") == "US East (N. Virginia)"
        assert AWSPricingProvider._region_to_location("eu-west-1") == "EU (Ireland)"
        assert AWSPricingProvider._region_to_location("ap-unknown-1") == "ap-unknown-1"


# ---------------------------------------------------------------------------
# AzurePricingProvider
# ---------------------------------------------------------------------------


class TestAzurePricingProvider:
    def test_parses_retail_price(self) -> None:
        provider = AzurePricingProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Items": [{"unitPrice": 0.096, "meterName": "D2s v3"}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response):
            result = provider.get_compute_hourly("Standard_D2s_v3", "eastus")

        assert result == pytest.approx(0.096)

    def test_returns_none_on_empty_items(self) -> None:
        provider = AzurePricingProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {"Items": []}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response):
            result = provider.get_compute_hourly("Unknown_SKU", "eastus")

        assert result is None

    def test_returns_none_on_network_error(self) -> None:
        provider = AzurePricingProvider()

        with patch("httpx.get", side_effect=Exception("timeout")):
            result = provider.get_compute_hourly("Standard_D2s_v3", "eastus")

        assert result is None


# ---------------------------------------------------------------------------
# Pricing fallback with hourly_override
# ---------------------------------------------------------------------------


class TestPricingFallback:
    def test_ec2_uses_override_when_provided(self) -> None:
        from cloud.aws.resource_collector import _daily_cost_for_ec2

        cost = _daily_cost_for_ec2("t3.medium", "running", hourly_override=0.05)
        assert cost == pytest.approx(0.05 * 24)

    def test_ec2_uses_table_when_override_is_none(self) -> None:
        from cloud.aws.resource_collector import _daily_cost_for_ec2

        cost = _daily_cost_for_ec2("t3.medium", "running", hourly_override=None)
        assert cost == pytest.approx(0.0416 * 24)

    def test_ebs_uses_override_when_provided(self) -> None:
        from cloud.aws.resource_collector import _daily_cost_for_ebs

        cost = _daily_cost_for_ebs("gp3", 100, gb_month_override=0.10)
        assert cost == pytest.approx(100 * 0.10 / 30, rel=1e-4)

    def test_azure_vm_uses_override_when_provided(self) -> None:
        from cloud.azure.resource_collector import _daily_cost_for_vm

        cost = _daily_cost_for_vm("Standard_D2s_v3", "running", hourly_override=0.10)
        assert cost == pytest.approx(0.10 * 24)

    def test_gcp_instance_uses_override_when_provided(self) -> None:
        from cloud.gcp.resource_collector import _daily_cost_for_instance

        cost = _daily_cost_for_instance("n1-standard-2", "RUNNING", hourly_override=0.10)
        assert cost == pytest.approx(0.10 * 24)
