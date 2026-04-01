# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from cloud.aws.cost_collector import AWSCostCollector
from cloud.aws.resource_collector import (
    AWSResourceCollector,
    _daily_cost_for_ec2,
    _daily_cost_for_ebs,
    _daily_cost_for_elb,
    _daily_cost_for_eks,
    _daily_cost_for_nat,
    _daily_cost_for_rds,
)


class TestAWSCostCollector:
    def test_collect_costs_parses_response(self) -> None:
        mock_session = MagicMock()
        mock_ce = MagicMock()
        mock_session.client.return_value = mock_ce

        mock_ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2025-03-01", "End": "2025-03-02"},
                    "Groups": [
                        {
                            "Keys": ["Amazon Elastic Compute Cloud - Compute", "us-east-1"],
                            "Metrics": {"UnblendedCost": {"Amount": "42.50", "Unit": "USD"}},
                        },
                        {
                            "Keys": ["Amazon Simple Storage Service", "us-east-1"],
                            "Metrics": {"UnblendedCost": {"Amount": "0.0", "Unit": "USD"}},
                        },
                    ],
                }
            ],
        }

        collector = AWSCostCollector(mock_session, "123456789012")
        results = collector.collect_costs(date(2025, 3, 1), date(2025, 3, 2))

        assert len(results) == 1  # zero-cost S3 filtered out
        assert results[0].service == "Amazon Elastic Compute Cloud - Compute"
        assert results[0].cost_usd == 42.50
        assert results[0].provider == "aws"

    def test_collect_costs_handles_pagination(self) -> None:
        mock_session = MagicMock()
        mock_ce = MagicMock()
        mock_session.client.return_value = mock_ce

        mock_ce.get_cost_and_usage.side_effect = [
            {
                "ResultsByTime": [
                    {
                        "TimePeriod": {"Start": "2025-03-01", "End": "2025-03-02"},
                        "Groups": [
                            {
                                "Keys": ["EC2", "us-east-1"],
                                "Metrics": {"UnblendedCost": {"Amount": "10.0", "Unit": "USD"}},
                            }
                        ],
                    }
                ],
                "NextPageToken": "token123",
            },
            {
                "ResultsByTime": [
                    {
                        "TimePeriod": {"Start": "2025-03-02", "End": "2025-03-03"},
                        "Groups": [
                            {
                                "Keys": ["EC2", "us-east-1"],
                                "Metrics": {"UnblendedCost": {"Amount": "15.0", "Unit": "USD"}},
                            }
                        ],
                    }
                ],
            },
        ]

        collector = AWSCostCollector(mock_session, "123456789012")
        results = collector.collect_costs(date(2025, 3, 1), date(2025, 3, 3))

        assert len(results) == 2
        assert mock_ce.get_cost_and_usage.call_count == 2

    def test_collect_costs_empty_response(self) -> None:
        mock_session = MagicMock()
        mock_ce = MagicMock()
        mock_session.client.return_value = mock_ce
        mock_ce.get_cost_and_usage.return_value = {"ResultsByTime": []}

        collector = AWSCostCollector(mock_session, "123456789012")
        results = collector.collect_costs(date(2025, 3, 1), date(2025, 3, 2))
        assert results == []


class TestAWSPricing:
    def test_ec2_running_cost(self) -> None:
        cost = _daily_cost_for_ec2("t3.medium", "running")
        assert cost == pytest.approx(0.0416 * 24)

    def test_ec2_stopped_zero(self) -> None:
        assert _daily_cost_for_ec2("t3.medium", "stopped") == 0.0

    def test_ec2_unknown_type_zero(self) -> None:
        assert _daily_cost_for_ec2("x99.mega", "running") == 0.0

    def test_ebs_gp3(self) -> None:
        cost = _daily_cost_for_ebs("gp3", 100)
        assert cost == pytest.approx(100 * 0.08 / 30, rel=1e-4)

    def test_ebs_io1(self) -> None:
        cost = _daily_cost_for_ebs("io1", 50)
        assert cost == pytest.approx(50 * 0.125 / 30, rel=1e-4)

    def test_elb_application(self) -> None:
        assert _daily_cost_for_elb("application") == pytest.approx(0.0225 * 24, rel=1e-4)

    def test_elb_network(self) -> None:
        assert _daily_cost_for_elb("network") == pytest.approx(0.0225 * 24, rel=1e-4)

    def test_nat_gateway(self) -> None:
        assert _daily_cost_for_nat() == pytest.approx(0.045 * 24, rel=1e-4)

    def test_eks_control_plane(self) -> None:
        assert _daily_cost_for_eks() == pytest.approx(0.10 * 24, rel=1e-4)

    def test_rds_single_az(self) -> None:
        cost = _daily_cost_for_rds("db.t3.medium", 100, "gp3", False)
        expected = 0.068 * 24 + 100 * 0.08 / 30
        assert cost == pytest.approx(expected, rel=1e-4)

    def test_rds_multi_az_doubles_compute(self) -> None:
        single = _daily_cost_for_rds("db.m5.large", 50, "gp3", False)
        multi = _daily_cost_for_rds("db.m5.large", 50, "gp3", True)
        # Multi-AZ should have higher cost (roughly double compute)
        assert multi > single


class TestAWSResourceCollector:
    def test_collect_ec2(self) -> None:
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2

        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-abc123",
                                "State": {"Name": "running"},
                                "InstanceType": "t3.medium",
                                "Tags": [{"Key": "Name", "Value": "web-1"}],
                                "LaunchTime": "2025-01-01T00:00:00Z",
                                "VpcId": "vpc-123",
                            }
                        ]
                    }
                ]
            }
        ]

        collector = AWSResourceCollector(mock_session, "123456789012", ["us-east-1"])
        results = collector._collect_ec2("us-east-1")

        assert len(results) == 1
        assert results[0].resource_id == "i-abc123"
        assert results[0].name == "web-1"
        assert results[0].state == "running"
        assert results[0].metadata["instance_type"] == "t3.medium"

    def test_collect_ebs(self) -> None:
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2

        mock_paginator = MagicMock()
        mock_ec2.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                "Volumes": [
                    {
                        "VolumeId": "vol-abc",
                        "Attachments": [],
                        "VolumeType": "gp3",
                        "Size": 100,
                        "Iops": 3000,
                        "Encrypted": True,
                        "Tags": [],
                    }
                ]
            }
        ]

        collector = AWSResourceCollector(mock_session, "123456789012", ["us-east-1"])
        results = collector._collect_ebs("us-east-1")

        assert len(results) == 1
        assert results[0].state == "unattached"
        assert results[0].metadata["size_gb"] == 100

    def test_collect_elb(self) -> None:
        mock_session = MagicMock()
        mock_elbv2 = MagicMock()
        mock_session.client.return_value = mock_elbv2

        mock_paginator = MagicMock()
        mock_elbv2.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                "LoadBalancers": [
                    {
                        "LoadBalancerArn": (
                            "arn:aws:elbv2:us-east-1:123:lb/app/my-lb/abc"
                        ),
                        "LoadBalancerName": "my-lb",
                        "State": {"Code": "active"},
                        "Type": "application",
                        "Scheme": "internet-facing",
                        "DNSName": "my-lb-123.elb.amazonaws.com",
                        "VpcId": "vpc-123",
                    }
                ]
            }
        ]

        collector = AWSResourceCollector(mock_session, "123456789012", ["us-east-1"])
        results = collector._collect_elb("us-east-1")

        assert len(results) == 1
        assert results[0].name == "my-lb"
        assert results[0].service == "ELB"
        assert results[0].daily_cost > 0

    def test_collect_rds(self) -> None:
        mock_session = MagicMock()
        mock_rds = MagicMock()
        mock_session.client.return_value = mock_rds

        mock_paginator = MagicMock()
        mock_rds.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                "DBInstances": [
                    {
                        "DBInstanceArn": "arn:aws:rds:us-east-1:123:db:mydb",
                        "DBInstanceIdentifier": "mydb",
                        "DBInstanceStatus": "available",
                        "DBInstanceClass": "db.t3.medium",
                        "Engine": "mysql",
                        "EngineVersion": "8.0",
                        "AllocatedStorage": 100,
                        "MultiAZ": False,
                        "StorageType": "gp3",
                        "TagList": [],
                        "DBSubnetGroup": {"VpcId": "vpc-123"},
                    }
                ]
            }
        ]

        collector = AWSResourceCollector(mock_session, "123456789012", ["us-east-1"])
        results = collector._collect_rds("us-east-1")

        assert len(results) == 1
        assert results[0].type == "database"
        assert results[0].service == "RDS"
        assert results[0].metadata["engine"] == "mysql"
        assert results[0].daily_cost > 0

    def test_collect_s3(self) -> None:
        mock_session = MagicMock()
        mock_s3 = MagicMock()
        mock_session.client.return_value = mock_s3

        mock_s3.list_buckets.return_value = {
            "Buckets": [{"Name": "my-bucket", "CreationDate": "2025-01-01T00:00:00Z"}]
        }
        mock_s3.get_bucket_location.return_value = {"LocationConstraint": "us-east-1"}

        collector = AWSResourceCollector(mock_session, "123456789012", ["us-east-1"])
        results = collector._collect_s3()

        assert len(results) == 1
        assert results[0].service == "S3"
        assert results[0].type == "storage"
        assert results[0].name == "my-bucket"

    def test_collect_lambda(self) -> None:
        mock_session = MagicMock()
        mock_lambda = MagicMock()
        mock_session.client.return_value = mock_lambda

        mock_paginator = MagicMock()
        mock_lambda.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                "Functions": [
                    {
                        "FunctionArn": "arn:aws:lambda:us-east-1:123:function:my-fn",
                        "FunctionName": "my-fn",
                        "Runtime": "python3.11",
                        "MemorySize": 256,
                        "Timeout": 30,
                        "CodeSize": 1024,
                        "Handler": "index.handler",
                        "LastModified": "2025-01-01T00:00:00Z",
                    }
                ]
            }
        ]

        collector = AWSResourceCollector(mock_session, "123456789012", ["us-east-1"])
        results = collector._collect_lambda("us-east-1")

        assert len(results) == 1
        assert results[0].type == "serverless"
        assert results[0].service == "Lambda"
        assert results[0].metadata["runtime"] == "python3.11"

    def test_collect_vpn(self) -> None:
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2

        mock_ec2.describe_vpn_connections.return_value = {
            "VpnConnections": [
                {
                    "VpnConnectionId": "vpn-123",
                    "State": "available",
                    "Tags": [{"Key": "Name", "Value": "my-vpn"}],
                    "VpnGatewayId": "vgw-123",
                    "CustomerGatewayId": "cgw-123",
                    "Type": "ipsec.1",
                }
            ]
        }

        collector = AWSResourceCollector(mock_session, "123456789012", ["us-east-1"])
        results = collector._collect_vpn("us-east-1")

        assert len(results) == 1
        assert results[0].service == "VPN"
        assert results[0].daily_cost > 0

    def test_collect_cloudfront(self) -> None:
        mock_session = MagicMock()
        mock_cf = MagicMock()
        mock_session.client.return_value = mock_cf

        mock_paginator = MagicMock()
        mock_cf.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                "DistributionList": {
                    "Items": [
                        {
                            "ARN": "arn:aws:cloudfront::123:distribution/ABC",
                            "DomainName": "d123.cloudfront.net",
                            "Enabled": True,
                            "PriceClass": "PriceClass_100",
                            "HttpVersion": "http2",
                            "Origins": {"Items": [{"Id": "origin-1"}]},
                        }
                    ]
                }
            }
        ]

        collector = AWSResourceCollector(mock_session, "123456789012", ["us-east-1"])
        results = collector._collect_cloudfront()

        assert len(results) == 1
        assert results[0].service == "CloudFront"
        assert results[0].region == "global"

    def test_collect_api_gateway(self) -> None:
        mock_session = MagicMock()
        mock_apigw = MagicMock()
        mock_session.client.return_value = mock_apigw

        mock_apigw.get_rest_apis.return_value = {
            "items": [
                {
                    "id": "abc123",
                    "name": "my-api",
                    "description": "My API",
                    "endpointConfiguration": {"types": ["REGIONAL"]},
                    "createdDate": "2025-01-01",
                    "tags": {"env": "prod"},
                }
            ]
        }

        collector = AWSResourceCollector(mock_session, "123456789012", ["us-east-1"])
        results = collector._collect_api_gateway("us-east-1")

        assert len(results) == 1
        assert results[0].service == "APIGateway"
        assert results[0].type == "managed_service"


class TestAWSCpuMetrics:
    def test_enriches_running_ec2_with_cpu(self) -> None:
        from cost_model.models import ResourceSnapshot

        mock_session = MagicMock()
        mock_cw = MagicMock()
        mock_session.client.return_value = mock_cw
        mock_cw.get_metric_statistics.return_value = {
            "Datapoints": [
                {"Average": 3.5},
                {"Average": 2.1},
            ]
        }

        snap = ResourceSnapshot(
            resource_id="i-abc123", provider="aws", account_id="123",
            type="compute", service="EC2", name="test", region="us-east-1",
            daily_cost=1.0, monthly_cost_estimate=30.0, currency="USD",
            state="running", metadata={"instance_type": "t3.medium"},
            snapshot_time=datetime.now(UTC),
        )

        collector = AWSResourceCollector(mock_session, "123", ["us-east-1"])
        result = collector.collect_cpu_metrics([snap])

        assert len(result) == 1
        assert result[0].metadata["avg_cpu_percent"] == pytest.approx(2.8, rel=0.1)

    def test_skips_stopped_instances(self) -> None:
        from cost_model.models import ResourceSnapshot

        mock_session = MagicMock()

        snap = ResourceSnapshot(
            resource_id="i-stopped", provider="aws", account_id="123",
            type="compute", service="EC2", name="stopped", region="us-east-1",
            daily_cost=0.0, monthly_cost_estimate=0.0, currency="USD",
            state="stopped", metadata={},
            snapshot_time=datetime.now(UTC),
        )

        collector = AWSResourceCollector(mock_session, "123", ["us-east-1"])
        result = collector.collect_cpu_metrics([snap])

        assert "avg_cpu_percent" not in result[0].metadata
        mock_session.client.assert_not_called()

    def test_graceful_on_cloudwatch_error(self) -> None:
        from cost_model.models import ResourceSnapshot

        mock_session = MagicMock()
        mock_cw = MagicMock()
        mock_session.client.return_value = mock_cw
        mock_cw.get_metric_statistics.side_effect = Exception("access denied")

        snap = ResourceSnapshot(
            resource_id="i-err", provider="aws", account_id="123",
            type="compute", service="EC2", name="err", region="us-east-1",
            daily_cost=1.0, monthly_cost_estimate=30.0, currency="USD",
            state="running", metadata={"instance_type": "t3.medium"},
            snapshot_time=datetime.now(UTC),
        )

        collector = AWSResourceCollector(mock_session, "123", ["us-east-1"])
        result = collector.collect_cpu_metrics([snap])

        assert "avg_cpu_percent" not in result[0].metadata
