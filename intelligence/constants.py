# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

# Anomaly detection thresholds
COST_SPIKE_MULTIPLIER: float = 1.25
NEW_HIGH_COST_DAILY_THRESHOLD_USD: float = 50.0
SUDDEN_SCALING_MULTIPLIER: float = 2.0

# Waste detection thresholds
UNATTACHED_DISK_HOURS: int = 24
STOPPED_INSTANCE_DAYS: int = 7
IDLE_NAT_GB_PER_DAY: float = 1.0
OVERSIZED_CPU_PERCENT: float = 10.0
OVERSIZED_CPU_DAYS: int = 3

# Forecast parameters
FORECAST_LOOKBACK_DAYS: int = 14

# Contributor analysis
TOP_N_RESULTS: int = 10
CONTRIBUTOR_LOOKBACK_DAYS: int = 30
