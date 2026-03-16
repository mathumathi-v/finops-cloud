# finops-agent

**Open-source, CLI-first, multi-cloud infrastructure cost reasoning agent.**

finops-agent connects to your cloud billing APIs, collects cost and resource data,
runs deterministic intelligence (anomaly detection, waste detection, forecasting),
and uses an LLM to generate plain-English explanations and saving recommendations.

It is **read-only by design** — it will never create, modify, or delete any cloud resource.

```
$ finops summary

                  Cost Summary (since 2025-06-01)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Service                                ┃ Cost (USD) ┃ % of Total ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ Amazon Elastic Compute Cloud - Compute │ $4,218.30  │ 48.7%      │
│ Amazon Relational Database Service     │ $1,892.45  │ 21.8%      │
│ EC2 - Other                            │ $1,105.60  │ 12.8%      │
│ Amazon CloudFront                      │ $412.15    │  4.8%      │
│ Amazon Elastic Load Balancing          │ $328.90    │  3.8%      │
│ Amazon Simple Storage Service          │ $186.40    │  2.2%      │
└────────────────────────────────────────┴────────────┴────────────┘
Total: $8,662.50

$ finops explain-bill

Your AWS bill for June 2025 totals $8,662. EC2 compute accounts for nearly half
of all spend at $4,218. The three largest RDS instances in eu-west-2 contribute
$1,892 and have not changed size since Q1 — worth reviewing for reserved instance
coverage. Data transfer costs buried in EC2-Other ($1,105) suggest traffic
leaving the region, likely from your CloudFront distribution to origin.
Two NAT Gateways had less than 500MB of traffic this week — shutting them down
would save approximately $65/month.
```

---

## Why finops-agent?

Most cloud cost tools are dashboards you never check or SaaS products that want
your billing data on their servers. finops-agent is different:

- **CLI-first** — runs where you work, outputs to your terminal
- **Local-only data** — cost data stays in a local SQLite database, never leaves your machine
- **Real reasoning** — deterministic anomaly/waste detection first, LLM for explanation only
- **Read-only** — needs only viewer/read permissions, will never touch your infrastructure
- **BYO LLM** — works with OpenAI, Anthropic, Groq, Gemini, Ollama, or any OpenAI-compatible endpoint
- **Zero telemetry** — no tracking, no analytics, no phone-home

---

## Supported Clouds

| Cloud | Status | Cost Data | Resources Collected |
|-------|--------|-----------|-------------------|
| AWS | **Supported** | Cost Explorer API (daily, per service/region) | EC2, EBS, RDS, ELB/ALB, NAT Gateway, EKS |
| GCP | **Supported** | BigQuery billing export (daily, per service/region) | Compute Engine VMs, Persistent Disks, Load Balancers, GKE |
| Azure | Coming soon | Cost Management API | VMs, Managed Disks, Load Balancers, AKS |

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/your-org/finops-agent.git
cd finops-agent
pip install -e .
```

For GCP support, install the optional GCP dependencies:

```bash
pip install -e ".[gcp]"
```

For development (linting, type checking, tests):

```bash
pip install -e ".[dev]"
```

### 2. Configure your cloud

See the full setup guides below:
- [AWS Setup](#aws-setup)
- [GCP Setup](#gcp-setup)
- [Azure Setup](#azure-setup-coming-soon)

### 3. Configure an LLM (optional)

See [LLM Setup](#llm-setup).

### 4. Collect and analyze

```bash
finops collect               # Pull cost + resource data
finops summary               # Where is the money going?
finops explain-bill          # AI-powered full bill breakdown
finops find-waste            # Unattached disks, idle resources
finops explain-spike         # What caused that cost jump?
finops forecast              # What will next month cost?
```

---

## AWS Setup

### Required permissions

The agent needs **read-only** access only. Attach the AWS managed policy:

```
arn:aws:iam::aws:policy/ReadOnlyAccess
```

Or use this minimal custom policy covering exactly what the agent calls:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "FinopsAgentReadOnly",
      "Effect": "Allow",
      "Action": [
        "sts:GetCallerIdentity",
        "ce:GetCostAndUsage",
        "ce:GetCostForecast",
        "ec2:DescribeInstances",
        "ec2:DescribeVolumes",
        "ec2:DescribeNatGateways",
        "ec2:DescribeAddresses",
        "elasticloadbalancing:DescribeLoadBalancers",
        "elasticloadbalancing:DescribeTargetGroups",
        "eks:ListClusters",
        "eks:DescribeCluster",
        "eks:ListNodegroups",
        "eks:DescribeNodegroup"
      ],
      "Resource": "*"
    }
  ]
}
```

The agent will **never** call any of the following (or any write equivalent):
`ec2:StopInstances`, `ec2:TerminateInstances`, `ec2:DeleteVolume`,
`rds:StopDBInstance`, `eks:DeleteCluster`, or any `Create*`, `Delete*`,
`Modify*`, `Update*`, `Put*` action.

### Service enablement

AWS Cost Explorer must be enabled in your account. It is **on by default** for
all accounts. If it was manually disabled, re-enable it at:

> Billing Console → Cost Explorer → Enable Cost Explorer

No other service enablement is required.

### Authentication methods

**Option 1 — AWS CLI profile (recommended for local use)**

```bash
aws configure                      # creates ~/.aws/credentials
finops config set aws.enabled true
finops config set aws.profile default
finops config set aws.regions '["us-east-1", "eu-west-2"]'
```

**Option 2 — Explicit credentials**

```bash
finops config set aws.enabled true
finops config set aws.access_key_id AKIAIOSFODNN7EXAMPLE
finops config set aws.secret_access_key wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
finops config set aws.regions '["us-east-1"]'
```

**Option 3 — IAM role / instance profile**

If finops-agent is running on an EC2 instance or ECS task with an attached IAM
role, set `profile` to empty and leave credentials blank — boto3 will pick up
the instance metadata credentials automatically.

```bash
finops config set aws.enabled true
finops config set aws.regions '["us-east-1", "eu-west-2"]'
```

### How AWS data flows

```
AWS Cost Explorer API
  └─ GetCostAndUsage (daily, grouped by SERVICE + REGION)
       └─ CostSnapshot (per service/region/day)
            └─ SQLite cost_snapshots table
                 └─ intelligence engine (anomaly, forecast, contributors)

EC2 / EBS / ELB / NAT / EKS Describe* APIs
  └─ ResourceSnapshot (per resource, with state + metadata)
       └─ SQLite resource_snapshots table
            └─ intelligence engine (waste detection)
```

---

## GCP Setup

### Required IAM roles

Grant these roles to the principal (user account or service account) that
finops-agent authenticates as:

| Role | Purpose |
|------|---------|
| `roles/viewer` | Read Compute Engine VMs, disks, load balancers, GKE clusters |
| `roles/billing.viewer` | View billing account information |
| `roles/bigquery.dataViewer` | Read the billing export BigQuery dataset |
| `roles/bigquery.jobUser` | Run BigQuery queries against the billing export |

To grant via CLI:

```bash
# Replace PROJECT_ID and PRINCIPAL (e.g. user:you@example.com or serviceAccount:sa@project.iam.gserviceaccount.com)
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="PRINCIPAL" \
  --role="roles/viewer"

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="PRINCIPAL" \
  --role="roles/bigquery.dataViewer"

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="PRINCIPAL" \
  --role="roles/bigquery.jobUser"
```

The billing.viewer role must be granted at the **billing account level**, not
the project level:

```bash
gcloud billing accounts add-iam-policy-binding BILLING_ACCOUNT_ID \
  --member="PRINCIPAL" \
  --role="roles/billing.viewer"
```

### APIs to enable

These GCP APIs must be enabled in your project before the collector will work:

```bash
gcloud services enable compute.googleapis.com
gcloud services enable container.googleapis.com
gcloud services enable bigquery.googleapis.com
gcloud services enable cloudbilling.googleapis.com
```

Or enable them in the GCP Console under **APIs & Services → Library**.

### Setting up the billing export (required for cost data)

GCP does not have a direct cost API equivalent to AWS Cost Explorer. Cost data
must come from a **BigQuery billing export**, which you enable once and GCP
populates daily.

**Steps:**

1. Go to **GCP Console → Billing → select your billing account → Billing export**
2. Under **Standard usage cost**, click **Edit settings**
3. Set:
   - **Project**: the project where finops-agent will query (e.g. `my-project`)
   - **Dataset name**: create a new dataset, e.g. `gcp_billing_export`
4. Click **Save**
5. Wait **24–48 hours** for the first data to appear

The table name will be automatically created as:
```
gcp_billing_export_v1_XXXXXX_XXXXXX_XXXXXX
```
where the `X` segments are your billing account ID with dashes removed.

Find your exact table name by running:

```bash
bq ls --project_id=YOUR_PROJECT gcp_billing_export
```

Then configure finops-agent:

```bash
finops config set gcp.enabled true
finops config set gcp.project_id "my-project"
finops config set gcp.billing_dataset "gcp_billing_export"
finops config set gcp.billing_table "gcp_billing_export_v1_XXXXXX_XXXXXX_XXXXXX"
```

Resource collection (VMs, disks, GKE) works immediately without the billing
export — cost data requires it.

### Authentication methods

**Option 1 — gcloud Application Default Credentials (recommended for local use)**

```bash
gcloud auth application-default login
finops config set gcp.enabled true
finops config set gcp.project_id "my-project"
# leave credentials_file empty — ADC is used automatically
```

**Option 2 — Service account key file (recommended for servers and CI)**

```bash
# Create a service account
gcloud iam service-accounts create finops-agent \
  --display-name="finops-agent reader"

# Grant required roles (see above)
gcloud projects add-iam-policy-binding my-project \
  --member="serviceAccount:finops-agent@my-project.iam.gserviceaccount.com" \
  --role="roles/viewer"

# Download key
gcloud iam service-accounts keys create ~/finops-agent-sa.json \
  --iam-account=finops-agent@my-project.iam.gserviceaccount.com

# Configure finops-agent
finops config set gcp.credentials_file "~/finops-agent-sa.json"
finops config set gcp.project_id "my-project"
```

**Option 3 — Workload Identity (for GKE / Cloud Run deployments)**

Attach the service account to your Kubernetes service account. Leave
`credentials_file` empty — the GCP metadata server provides credentials
automatically when running inside GCP.

### How GCP data flows

```
BigQuery billing export table
  └─ SQL query (daily totals grouped by service + region)
       └─ CostSnapshot (per service/region/day)
            └─ SQLite cost_snapshots table
                 └─ intelligence engine (anomaly, forecast, contributors)

Compute Engine aggregatedList APIs (instances, disks, forwarding rules)
Container API listClusters
  └─ ResourceSnapshot (per resource, with state + metadata)
       └─ SQLite resource_snapshots table
            └─ intelligence engine (waste detection: unattached disks, stopped VMs)
```

---

## Azure Setup (coming soon)

Azure support is under active development. When released, it will use the
**Azure Cost Management API** and require:

### Required roles

| Role | Scope | Purpose |
|------|-------|---------|
| `Reader` | Subscription | List VMs, disks, load balancers, AKS clusters |
| `Cost Management Reader` | Subscription | Read cost and usage data |

### Authentication methods

**Option 1 — Service principal (recommended for servers)**

```bash
az ad sp create-for-rbac --name finops-agent --role Reader \
  --scopes /subscriptions/YOUR_SUBSCRIPTION_ID
```

This outputs `appId`, `password`, and `tenant`. Configure:

```bash
finops config set azure.enabled true
finops config set azure.subscription_id "YOUR_SUBSCRIPTION_ID"
finops config set azure.tenant_id "YOUR_TENANT_ID"
finops config set azure.client_id "APP_ID"
finops config set azure.client_secret "PASSWORD"
```

**Option 2 — Azure CLI credentials (for local use)**

```bash
az login
# leave client_id and client_secret empty — az login credentials are used
```

**Option 3 — Managed Identity (for Azure VMs / AKS)**

Assign the managed identity the `Reader` and `Cost Management Reader` roles,
then leave all credential fields empty.

### Resource providers to register

```bash
az provider register --namespace Microsoft.Compute
az provider register --namespace Microsoft.ContainerService
az provider register --namespace Microsoft.CostManagement
az provider register --namespace Microsoft.Network
```

---

## LLM Setup

The LLM is used **only for generating explanations**. All anomaly detection,
waste detection, and forecasting logic is deterministic and runs without any LLM.
If no LLM is configured, commands still work and show structured data.

### Option 1 — Groq (free tier, recommended)

Sign up at [console.groq.com](https://console.groq.com/keys) for a free API key.
Groq provides fast inference on Llama models at no cost for typical usage.

```bash
finops config set llm.provider local
finops config set llm.model llama-3.3-70b-versatile
finops config set llm.api_key gsk_your_key_here
finops config set llm.base_url https://api.groq.com/openai/v1
```

### Option 2 — Google Gemini (free tier)

Sign up at [aistudio.google.com](https://aistudio.google.com/apikey) for a free API key.

```bash
finops config set llm.provider local
finops config set llm.model gemini-2.0-flash
finops config set llm.api_key AIza_your_key_here
finops config set llm.base_url https://generativelanguage.googleapis.com/v1beta/openai
```

### Option 3 — OpenAI

```bash
finops config set llm.provider openai
finops config set llm.model gpt-4o
finops config set llm.api_key sk-your_key_here
```

### Option 4 — Anthropic

```bash
finops config set llm.provider anthropic
finops config set llm.model claude-sonnet-4-6
finops config set llm.api_key sk-ant-your_key_here
```

### Option 5 — Ollama (fully local, free)

Install [Ollama](https://ollama.ai) and pull a model:

```bash
ollama pull llama3.1
```

Then configure:

```bash
finops config set llm.provider local
finops config set llm.model llama3.1
finops config set llm.base_url http://localhost:11434/v1
finops config set llm.api_key ollama
```

---

## Commands

| Command | Description |
|---------|-------------|
| `finops collect` | Pull cost data (last 30 days) and resource metadata |
| `finops summary` | Cost breakdown by service and region |
| `finops explain-bill` | Full bill analysis with LLM-powered reasoning |
| `finops explain-spike` | Detect cost anomalies and explain likely causes |
| `finops top-cost` | Top 10 most expensive resources |
| `finops find-waste` | Find unattached disks, stopped instances, idle NAT gateways |
| `finops forecast` | Monthly cost projection with trend analysis |
| `finops config set` | Set a configuration value |
| `finops config get` | Read a configuration value |
| `finops config path` | Show config file location |

### Global flags

```bash
--provider aws|gcp|azure|all    # Filter by cloud provider (default: aws)
--output json|table|plain       # Output format (default: table)
--since YYYY-MM-DD              # Filter from date
```

### Output formats

```bash
finops summary                    # Rich table (default)
finops summary --output json      # Machine-readable JSON
finops summary --output plain     # Plain text for piping / grep
```

---

## What It Detects

### Anomaly detection (deterministic, no LLM required)

| Rule | Trigger | Severity |
|------|---------|---------|
| Cost spike | Daily cost > 1.25x previous day for same service/region | high/medium/low by delta |
| New high-cost resource | New resource with daily cost > $50 | high |
| Sudden scaling | Compute instance count increased > 2x overnight | high |

### Waste detection (deterministic, no LLM required)

| Rule | Trigger | Estimated savings |
|------|---------|------------------|
| Unattached disk | EBS/PD with no attachments for > 24h | ~$0.08–$0.17/GB/month |
| Stopped instance | EC2/VM stopped for > 7 days (EBS charges continue) | Varies |
| Idle NAT Gateway | NAT with < 1GB transfer/day | ~$32.40/month each |
| Unused Elastic IP | EIP not attached to a running instance | ~$3.60/month each |

### Forecasting

- Monthly projection based on average daily cost over last 14 days
- Linear regression trend over last 14 days
- Trend classification: increasing, decreasing, or stable

---

## Architecture

```
                    ┌──────────────┐
                    │  CLI (Typer) │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
     ┌────────▼───┐  ┌─────▼──────┐  ┌─▼────────────┐
     │Intelligence│  │  LLM Layer │  │   Storage    │
     │  Engine    │  │            │  │  (SQLite)    │
     ├────────────┤  ├────────────┤  └──────────────┘
     │ Anomaly    │  │ Sanitizer  │
     │ Waste      │  │ Prompt Bld │
     │ Forecast   │  │ Client     │
     │ Contrib.   │  │ (OpenAI /  │
     └─────┬──────┘  │  Anthropic │
           │         │  Groq /    │
     ┌─────▼──────┐  │  Ollama)   │
     │  Cloud     │  └────────────┘
     │ Collectors │
     ├────────────┤
     │ AWS Cost   │  ← Cost Explorer API
     │ AWS Res.   │  ← EC2/EBS/ELB/NAT/EKS Describe*
     │ GCP Cost   │  ← BigQuery billing export
     │ GCP Res.   │  ← Compute/Container aggregatedList
     │ Azure*     │  ← Cost Management API (coming soon)
     └────────────┘
```

### Key design decisions

- **Deterministic first** — all detection logic runs without an LLM. The LLM only generates human-readable explanations from already-computed results.
- **Normalised model** — every cloud maps into `ResourceSnapshot` and `CostSnapshot` dataclasses, so intelligence rules are fully cloud-agnostic.
- **Prompt injection guard** — cloud-sourced strings (resource names, tags) are sanitized before insertion into LLM prompts. Account IDs, ARNs, IPs, and internal hostnames are redacted.
- **Graceful degradation** — if the LLM is unavailable or errors out, all commands fall back to structured data output.
- **Local-only storage** — SQLite at `~/.finops-agent/finops.db`. No external DB, no network dependency for storage.

---

## Project Structure

```
finops-agent/
├── cli/
│   ├── main.py                 # All commands: summary, collect, explain-bill, etc.
│   ├── config_loader.py        # YAML config loader with file permission checks
│   └── output.py               # Table, JSON, plain output helpers
├── cloud/
│   ├── base.py                 # CloudCollector abstract base class
│   ├── aws/
│   │   ├── collector.py        # Unified AWS collector
│   │   ├── cost_collector.py   # Cost Explorer API → CostSnapshot
│   │   └── resource_collector.py  # EC2, EBS, ELB, NAT Gateway, EKS
│   ├── gcp/
│   │   ├── collector.py        # Unified GCP collector + credential loading
│   │   ├── cost_collector.py   # BigQuery billing export → CostSnapshot
│   │   └── resource_collector.py  # Compute VMs, Disks, LBs, GKE
│   └── azure/                  # Coming soon
├── cost_model/
│   └── models.py               # ResourceSnapshot, CostSnapshot, AnomalyEvent
├── intelligence/
│   ├── anomaly.py              # Cost spikes, high-cost resources, scaling events
│   ├── waste.py                # Unattached disks, stopped instances, idle NATs
│   ├── forecast.py             # Linear regression projections
│   ├── contributors.py         # Top services, regions, resources by cost
│   └── constants.py            # All configurable thresholds in one place
├── llm/
│   ├── client.py               # OpenAI, Anthropic, and OpenAI-compatible (Groq/Ollama)
│   ├── prompt_builder.py       # Context-aware prompt construction
│   └── sanitizer.py            # Prompt injection guard + data redaction
├── storage/
│   ├── base.py                 # StorageAdapter abstract base class
│   └── sqlite_adapter.py       # SQLite with schema migration
├── tests/                      # 62 unit tests covering all modules
│   ├── conftest.py
│   ├── test_aws_collector.py
│   ├── test_gcp_collector.py
│   ├── test_cost_model.py
│   ├── test_intelligence.py
│   ├── test_llm.py
│   └── test_storage.py
├── config.yaml                 # Default config template
├── pyproject.toml
├── Makefile
├── SECURITY.md
└── CHANGELOG.md
```

---

## Configuration Reference

The agent looks for config in this order:
1. `~/.finops-agent/config.yaml` (user config, created by `finops config set`)
2. `./config.yaml` (local project config)

```yaml
aws:
  enabled: true
  profile: default              # AWS CLI profile (mutually exclusive with access_key_id)
  access_key_id: ""             # Explicit credentials (optional)
  secret_access_key: ""
  regions:
    - us-east-1
    - eu-west-2

gcp:
  enabled: false
  project_id: ""                # GCP project ID
  credentials_file: ""          # Path to service account JSON (empty = use ADC)
  billing_project_id: ""        # Project hosting the BigQuery dataset (defaults to project_id)
  billing_dataset: ""           # BigQuery dataset name (e.g. gcp_billing_export)
  billing_table: ""             # BigQuery table name (e.g. gcp_billing_export_v1_ABCDEF_123456)

azure:
  enabled: false
  subscription_id: ""
  tenant_id: ""
  client_id: ""                 # Service principal app ID (empty = use az login)
  client_secret: ""

llm:
  provider: local               # openai | anthropic | local (for Groq/Gemini/Ollama)
  api_key: ""
  model: llama-3.3-70b-versatile
  base_url: https://api.groq.com/openai/v1

storage:
  path: ~/.finops-agent/finops.db

scheduler:
  enabled: false
  interval_hours: 24
```

The config file is created at `~/.finops-agent/config.yaml` with `chmod 600`
permissions. The agent will **refuse to run** if the config file is readable by
group or other users.

---

## Development

```bash
pip install -e ".[dev]"   # Install with dev dependencies

make lint                  # ruff check
make typecheck             # mypy
make test                  # pytest (skips integration tests)
make check                 # all three
```

### Running integration tests (requires real credentials)

```bash
pytest -m integration      # runs live cloud API tests
```

Integration tests are skipped in CI by default via `pytest -m "not integration"`.

### Adding a new waste detection rule

1. Add the threshold constant to `intelligence/constants.py`
2. Add the detection function to `intelligence/waste.py`
3. Register it in `find_all_waste()`
4. Add a test in `tests/test_intelligence.py`

### Adding a new cloud provider

1. Create `cloud/<provider>/` with `cost_collector.py`, `resource_collector.py`, `collector.py`
2. Implement `CloudCollector` from `cloud/base.py`
3. Map resources to `ResourceSnapshot` and costs to `CostSnapshot`
4. Wire into `cli/main.py` in the `collect` command
5. Add `tests/test_<provider>_collector.py` with mocked API responses

---

## Security

- **Read-only cloud access** — the agent never calls write/mutate/delete APIs. See the explicit deny lists in each collector.
- **Credentials never logged** — credentials are loaded once at startup, never printed, stored in SQLite, or sent to the LLM.
- **Config file permissions** — the agent refuses to run if `config.yaml` is readable by group/others.
- **LLM data redaction** — account IDs, ARNs, IP addresses, and internal hostnames are stripped before any data is sent to the LLM.
- **Prompt injection guard** — cloud-sourced strings (resource names, tags) are sanitized and truncated before insertion into LLM prompts.
- **Zero telemetry** — no data ever leaves your machine except to the configured LLM endpoint.
- **Local storage only** — all cost data is stored in a local SQLite database.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and the full security contract.

---

## Roadmap

- [x] AWS Cost Explorer integration
- [x] AWS resource collection (EC2, EBS, ELB, NAT Gateway, EKS)
- [x] GCP BigQuery billing export integration
- [x] GCP resource collection (Compute VMs, Persistent Disks, Load Balancers, GKE)
- [x] Anomaly detection engine (cost spikes, new high-cost resources, scaling events)
- [x] Waste detection engine (unattached disks, stopped instances, idle NATs, unused EIPs)
- [x] Cost forecasting (linear regression + trend)
- [x] LLM-powered explanations (OpenAI, Anthropic, Groq, Gemini, Ollama)
- [x] CLI with table/JSON/plain output
- [ ] Azure Cost Management + resource collection (VMs, Disks, AKS)
- [ ] CloudWatch/Cloud Monitoring integration for CPU-based waste detection
- [ ] Scheduler daemon mode (`finops-agent run --mode daemon`)
- [ ] Kubernetes CronJob deployment
- [ ] Helm chart

---

## Contributing

Contributions are welcome. Before submitting a PR:

- One PR per issue — no mega-PRs
- Type hints on all function signatures, docstrings on all public methods
- Tests required for all new logic
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
- All PRs must pass `make check` (ruff + mypy + pytest)
- Never introduce a dependency with a GPL/AGPL/SSPL license
- The agent must remain read-only — PRs that add any write/mutate cloud operation will not be merged

---

## License

Apache-2.0. See [LICENSE](LICENSE) for the full text.

```
Copyright 2025 finops-agent contributors
SPDX-License-Identifier: Apache-2.0
```
