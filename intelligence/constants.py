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
IDLE_CPU_PERCENT_THRESHOLD: float = 5.0
IDLE_CPU_LOOKBACK_DAYS: int = 14

# Pattern-based waste detection thresholds
IDLE_CPU_P95_THRESHOLD: float = 15.0       # P95 CPU below this AND avg below threshold = truly idle
OFFHOURS_IDLE_CPU_THRESHOLD: float = 3.0   # CPU threshold for off-hours/weekend analysis
DECLINING_CPU_MIN_DROP_PERCENT: float = 40.0  # flag if CPU dropped by this % over lookback period
STOPPED_FIRST_SEEN_DAYS: int = 7           # flag stopped resources only after this many days

# Forecast parameters
FORECAST_LOOKBACK_DAYS: int = 14

# Contributor analysis
TOP_N_RESULTS: int = 10
CONTRIBUTOR_LOOKBACK_DAYS: int = 30
