# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

from datetime import date
from unittest.mock import MagicMock

from cloud.aws.cost_collector import AWSCostCollector
from cloud.aws.resource_collector import AWSResourceCollector


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
