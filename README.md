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
- **BYO LLM** — works with OpenAI, Anthropic, Amazon Bedrock, Groq, Gemini, Ollama, or any OpenAI-compatible endpoint
- **Zero telemetry** — no tracking, no analytics, no phone-home

---

## Supported Clouds

| Cloud | Status | Cost Data | Resources Collected |
|-------|--------|-----------|-------------------|
| AWS | **Supported** | Cost Explorer API + RI/Savings Plan tracking | EC2, EBS, RDS, S3, ELB/ALB, NAT Gateway, EKS, Lambda, VPN, CloudFront, API Gateway |
| GCP | **Supported** | BigQuery billing export (daily, per service/region) | Compute Engine VMs, Persistent Disks, Load Balancers, GKE, Cloud SQL, GCS, Cloud Run, Cloud Functions, BigQuery, Pub/Sub |
| Azure | **Supported** | Cost Management API (daily, per service/region) | VMs, Managed Disks, Load Balancers, AKS, Storage Accounts, App Service Plans, SQL Databases, Function Apps, VPN Gateways, CDN, Cosmos DB |
| OCI | **Supported** | Usage API (daily, per service/region) | Compute, Block Volumes, Load Balancers, OKE, Autonomous DB, Object Storage |

---

## Quick Start

### Prerequisites

#### System requirements

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| **Python** | 3.11+ | 3.12 also supported |
| **pip** | 22.0+ | Comes with Python 3.11 |
| **OS** | Linux, macOS, Windows (WSL) | Tested on Ubuntu 22.04/24.04, macOS 14+ |
| **Disk** | ~100 MB | For dependencies + SQLite database |
| **Network** | Outbound HTTPS (443) | To cloud APIs and optional LLM endpoint |

#### Python 3.11+

Check your version first:

```bash
python3 --version
```

If you're on Python 3.10 or older (common on Ubuntu 22.04), install 3.11 first:

```bash
# Ubuntu / Debian
sudo apt update
sudo apt install python3.11 python3.11-venv python3.11-dev -y

# macOS (Homebrew)
brew install python@3.11

# Verify
python3.11 --version   # should print 3.11.x
```

#### Cloud CLI tools (recommended, not required)

These CLI tools simplify authentication setup but are not strictly required — you can use explicit credentials instead.

| Cloud | CLI Tool | Install |
|-------|----------|---------|
| AWS | `aws` CLI v2 | `curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip && unzip awscliv2.zip && sudo ./aws/install` |
| GCP | `gcloud` CLI | `curl https://sdk.cloud.google.com \| bash` |
| Azure | `az` CLI | `curl -sL https://aka.ms/InstallAzureCLIDeb \| sudo bash` |
| OCI | `oci` CLI | `pip install oci-cli` |

#### Cloud-specific prerequisites

**AWS:**
- An AWS account with Cost Explorer enabled (on by default)
- IAM user/role with `ReadOnlyAccess` or the [minimal custom policy](#required-permissions) below
- For RI/Savings Plan tracking: `ce:GetSavingsPlansUtilization` and `ec2:DescribeReservedInstances` permissions

**GCP:**
- A GCP project with billing enabled
- BigQuery billing export configured (see [GCP Setup](#gcp-setup)) — required for cost data
- APIs enabled: Compute Engine, Container, BigQuery, Cloud Billing, Cloud SQL Admin, Cloud Storage, Cloud Run, Cloud Functions, Pub/Sub
- IAM roles: `roles/viewer`, `roles/bigquery.dataViewer`, `roles/bigquery.jobUser`

**Azure:**
- An Azure subscription
- `Reader` + `Cost Management Reader` roles assigned to the principal
- Resource providers registered: `Microsoft.Compute`, `Microsoft.ContainerService`, `Microsoft.CostManagement`, `Microsoft.Network`, `Microsoft.Storage`, `Microsoft.Web`, `Microsoft.Sql`, `Microsoft.DocumentDB`, `Microsoft.Cdn`

**OCI:**
- An OCI tenancy with a compartment to scan
- IAM policy: `Allow group finops-readers to read all-resources in tenancy`
- For cost data: `Allow group finops-readers to read usage-reports in tenancy`

### 1. Install

```bash
git clone https://github.com/mathumathi-v/finops-cloud.git
cd finops-cloud
```

**Create and activate a virtual environment using Python 3.11** (required — the package will silently install as UNKNOWN if you skip this and your system Python is < 3.11):

```bash
python3.11 -m venv ~/.venvs/finops
source ~/.venvs/finops/bin/activate
```

Install the package:

```bash
pip install .
```

> **Note:** `pip install -e .` (editable mode) is not supported because the build backend does not implement PEP 660. Use `pip install .` instead. If you need to make code changes, re-run `pip install .` after each change.

Verify the installation:

```bash
pip show finops-agent   # should show Version: 0.3.2
finops --help
```

To reactivate the environment in a new terminal session:

```bash
source ~/.venvs/finops/bin/activate
```

For cloud-specific dependencies:

```bash
pip install ".[gcp]"     # GCP support (Compute, GKE, Cloud SQL, GCS, Cloud Run, Functions, BigQuery, Pub/Sub)
pip install ".[azure]"   # Azure support (VMs, AKS, SQL DB, Cosmos DB, CDN, Functions, VPN)
pip install ".[oci]"     # OCI support (Compute, OKE, Autonomous DB, Object Storage)
pip install ".[gcp,azure,oci]"  # All clouds at once
```

For development (linting, type checking, tests):

```bash
pip install ".[dev]"
```

### Alternative: Docker

```bash
# Pull from Docker Hub
docker pull mathumathi247/finops-agent:latest

# Run any command — mount your cloud credentials read-only
docker run --rm \
  -v ~/.finops-agent:/home/finops/.finops-agent \
  -v ~/.aws:/home/finops/.aws:ro \
  mathumathi247/finops-agent:latest summary --provider aws

# GCP credentials
docker run --rm \
  -v ~/.finops-agent:/home/finops/.finops-agent \
  -v ~/.config/gcloud:/home/finops/.config/gcloud:ro \
  mathumathi247/finops-agent:latest collect --provider gcp

# Azure credentials
docker run --rm \
  -v ~/.finops-agent:/home/finops/.finops-agent \
  -v ~/.azure:/home/finops/.azure:ro \
  mathumathi247/finops-agent:latest summary --provider azure

# OCI credentials
docker run --rm \
  -v ~/.finops-agent:/home/finops/.finops-agent \
  -v ~/.oci:/home/finops/.oci:ro \
  mathumathi247/finops-agent:latest collect --provider oci
```

Or build locally:

```bash
docker build -t finops-agent:latest .
make docker-build
make docker-run CMD="collect --provider aws"
```

The image includes all cloud provider SDKs (AWS, GCP, Azure, OCI), runs as
non-root user, and persists data via the `~/.finops-agent` volume mount.

### 2. Configure your cloud

See the full setup guides below:
- [AWS Setup](#aws-setup)
- [GCP Setup](#gcp-setup)
- [Azure Setup](#azure-setup)
- [OCI Setup](#oci-setup)

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
        "ce:GetSavingsPlansUtilization",
        "ec2:DescribeInstances",
        "ec2:DescribeVolumes",
        "ec2:DescribeNatGateways",
        "ec2:DescribeAddresses",
        "ec2:DescribeVpnConnections",
        "ec2:DescribeReservedInstances",
        "elasticloadbalancing:DescribeLoadBalancers",
        "elasticloadbalancing:DescribeTargetGroups",
        "eks:ListClusters",
        "eks:DescribeCluster",
        "eks:ListNodegroups",
        "eks:DescribeNodegroup",
        "rds:DescribeDBInstances",
        "s3:ListAllMyBuckets",
        "s3:GetBucketLocation",
        "lambda:ListFunctions",
        "cloudfront:ListDistributions",
        "apigateway:GET"
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

### Verify credentials before collecting

```bash
aws sts get-caller-identity
```

This should return your account ID and IAM user/role ARN. If it fails, your
credentials are not configured correctly — re-run `aws configure`.

### How AWS data flows

```
AWS Cost Explorer API
  ├─ GetCostAndUsage (daily, grouped by SERVICE + REGION)
  │    └─ CostSnapshot (per service/region/day)
  ├─ GetSavingsPlansUtilization
  │    └─ SavingsPlanSnapshot (utilization %)
  └─ DescribeReservedInstances
       └─ SavingsPlanSnapshot (per RI)
            └─ SQLite savings_plan_snapshots table

EC2 / EBS / ELB / NAT / EKS / RDS / S3 / Lambda / VPN / CloudFront / API Gateway
  └─ ResourceSnapshot (per resource, with state + cost estimate)
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
gcloud services enable sqladmin.googleapis.com
gcloud services enable storage.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable cloudfunctions.googleapis.com
gcloud services enable pubsub.googleapis.com
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

Compute Engine / GKE / Cloud SQL / GCS / Cloud Run / Functions / BigQuery / Pub/Sub
  └─ ResourceSnapshot (per resource, with state + cost estimate)
       └─ SQLite resource_snapshots table
            └─ intelligence engine (waste detection)
```

---

## Azure Setup

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
az provider register --namespace Microsoft.Storage
az provider register --namespace Microsoft.Web
az provider register --namespace Microsoft.Sql
az provider register --namespace Microsoft.DocumentDB
az provider register --namespace Microsoft.Cdn
```

### How Azure data flows

```
Azure Cost Management API
  └─ Query (ActualCost, Daily, grouped by ServiceName + ResourceLocation)
       └─ CostSnapshot (per service/region/day)
            └─ SQLite cost_snapshots table
                 └─ intelligence engine (anomaly, forecast, contributors)

Compute / Network / ContainerService / Storage / Web / SQL / CosmosDB / CDN mgmt APIs
  └─ ResourceSnapshot (per resource, with state + cost estimate)
       └─ SQLite resource_snapshots table
            └─ intelligence engine (waste detection)
```

---

## OCI Setup

### Required policies

The agent needs **read-only** access only. Create a policy in your OCI tenancy:

```
Allow group finops-readers to read all-resources in tenancy
Allow group finops-readers to read usage-reports in tenancy
```

Or use a minimal set of statements:

```
Allow group finops-readers to read instances in tenancy
Allow group finops-readers to read volumes in tenancy
Allow group finops-readers to read volume-attachments in tenancy
Allow group finops-readers to read load-balancers in tenancy
Allow group finops-readers to read clusters in tenancy
Allow group finops-readers to read node-pools in tenancy
Allow group finops-readers to read autonomous-databases in tenancy
Allow group finops-readers to read buckets in tenancy
Allow group finops-readers to read objectstorage-namespaces in tenancy
Allow group finops-readers to read usage-reports in tenancy
```

### Authentication methods

**Option 1 — OCI CLI config (recommended for local use)**

```bash
oci setup config                  # creates ~/.oci/config
finops config set oci.enabled true
finops config set oci.compartment_id "ocid1.compartment.oc1..YOUR_COMPARTMENT"
```

**Option 2 — Custom config file and profile**

```bash
finops config set oci.enabled true
finops config set oci.compartment_id "ocid1.compartment.oc1..YOUR_COMPARTMENT"
finops config set oci.config_file "/path/to/oci/config"
finops config set oci.profile "PROD"
```

**Option 3 — Instance principal (for OCI compute instances)**

When running on an OCI instance with instance principal configured, the SDK
picks up credentials automatically from the instance metadata service.

### How OCI data flows

```
OCI Usage API (UsageapiClient)
  └─ RequestSummarizedUsages (DAILY, COST, grouped by service + region)
       └─ CostSnapshot (per service/region/day)
            └─ SQLite cost_snapshots table
                 └─ intelligence engine (anomaly, forecast, contributors)

Core / BlockStorage / LoadBalancer / ContainerEngine / Database / ObjectStorage APIs
  └─ ResourceSnapshot (per resource, with state + cost estimate)
       └─ SQLite resource_snapshots table
            └─ intelligence engine (waste detection)
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

### Option 5 — Amazon Bedrock (uses IAM credentials, no API key needed)

If you're already on AWS, Bedrock lets you use Nova, Titan, Claude, Llama, Mistral,
and other models via the Converse API — no separate API key required.

```bash
finops config set llm.provider bedrock
finops config set llm.model amazon.nova-pro-v1:0
finops config set llm.bedrock_region us-east-1
```

The agent uses your existing AWS credentials (CLI profile, IAM role, or env vars).
Make sure the IAM principal has `bedrock:InvokeModel` permission.

Other Bedrock model IDs you can use:
- `amazon.titan-text-premier-v1:0`
- `anthropic.claude-3-sonnet-20240229-v1:0`
- `meta.llama3-70b-instruct-v1:0`

### Option 6 — Ollama (fully local, free)

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
| `finops find-waste` | Find unattached disks, stopped instances/databases, idle NATs |
| `finops forecast` | Monthly cost projection with trend analysis |
| `finops config set` | Set a configuration value |
| `finops config get` | Read a configuration value |
| `finops config path` | Show config file location |

### Global flags

```bash
--provider aws|gcp|azure|oci|all  # Filter by cloud provider (default: aws)
--output json|table|plain         # Output format (default: table)
--since YYYY-MM-DD                # Filter from date
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
| Unattached disk | EBS/PD/ManagedDisk/BlockVolume with no attachments | ~$0.08–$0.17/GB/month |
| Stopped instance | EC2/VM/GCE/Compute stopped (disk charges continue) | Varies |
| Idle NAT Gateway | NAT with < 1GB transfer/day | ~$32.40/month each |
| Unused Elastic IP | EIP not attached to a running instance | ~$3.60/month each |
| Idle instance | EC2/VM/GCE/Compute with avg CPU < 5% over 14 days | Full instance cost |
| Stopped database | RDS/AzureSQL/CloudSQL/AutonomousDB in stopped state | Storage charges continue |

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
           │         │  Bedrock / │
           │         │  Groq /    │
     ┌─────▼──────┐  │  Ollama)   │
     │  Cloud     │  └────────────┘
     │ Collectors │
     ├────────────┤
     │ AWS Cost   │  ← Cost Explorer + RI/Savings Plans
     │ AWS Res.   │  ← EC2/EBS/RDS/S3/ELB/NAT/EKS/Lambda/VPN/CF/APIGW
     │ GCP Cost   │  ← BigQuery billing export
     │ GCP Res.   │  ← Compute/GKE/CloudSQL/GCS/Run/Functions/BQ/PubSub
     │ Azure Cost │  ← Cost Management API
     │ Azure Res. │  ← Compute/Network/Storage/Web/SQL/Cosmos/CDN
     │ OCI Cost   │  ← Usage API
     │ OCI Res.   │  ← Core/BlockStorage/LB/OKE/Database/ObjectStorage
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
│   │   └── resource_collector.py  # EC2, EBS, RDS, S3, ELB, NAT, EKS, Lambda, VPN, CloudFront, API GW
│   ├── gcp/
│   │   ├── collector.py        # Unified GCP collector + credential loading
│   │   ├── cost_collector.py   # BigQuery billing export → CostSnapshot
│   │   └── resource_collector.py  # Compute VMs, Disks, LBs, GKE, Cloud SQL, GCS, Run, Functions, BQ, Pub/Sub
│   ├── azure/
│   │   ├── collector.py        # Unified Azure collector + credential building
│   │   ├── cost_collector.py   # Cost Management API → CostSnapshot
│   │   └── resource_collector.py  # VMs, Disks, LBs, AKS, Storage, App Service, SQL, Functions, VPN, CDN, Cosmos
│   └── oci/
│       ├── collector.py        # Unified OCI collector + config loading
│       ├── cost_collector.py   # Usage API → CostSnapshot
│       └── resource_collector.py  # Compute, Block Volumes, LBs, OKE, Autonomous DB, Object Storage
├── cost_model/
│   └── models.py               # ResourceSnapshot, CostSnapshot, SavingsPlanSnapshot, AnomalyEvent
├── intelligence/
│   ├── anomaly.py              # Cost spikes, high-cost resources, scaling events
│   ├── waste.py                # Unattached disks, stopped instances, idle NATs
│   ├── forecast.py             # Linear regression projections
│   ├── contributors.py         # Top services, regions, resources by cost
│   └── constants.py            # All configurable thresholds in one place
├── llm/
│   ├── client.py               # OpenAI, Anthropic, Bedrock, and OpenAI-compatible (Groq/Ollama)
│   ├── prompt_builder.py       # Context-aware prompt construction
│   └── sanitizer.py            # Prompt injection guard + data redaction
├── storage/
│   ├── base.py                 # StorageAdapter abstract base class
│   └── sqlite_adapter.py       # SQLite with schema migration
├── tests/
│   ├── conftest.py
│   ├── test_aws_collector.py
│   ├── test_gcp_collector.py
│   ├── test_azure_collector.py
│   ├── test_oci_collector.py
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

oci:
  enabled: false
  compartment_id: ""            # Compartment OCID to scan for resources
  tenancy_id: ""                # Tenancy OCID (defaults to value in OCI config)
  config_file: ""               # Path to OCI config (empty = ~/.oci/config)
  profile: ""                   # OCI config profile (empty = DEFAULT)

llm:
  provider: local               # openai | anthropic | bedrock | local (for Groq/Gemini/Ollama)
  api_key: ""
  model: llama-3.3-70b-versatile
  base_url: https://api.groq.com/openai/v1
  bedrock_region: us-east-1     # AWS region for Bedrock (only used when provider=bedrock)

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

## Troubleshooting

### Package installs as UNKNOWN 0.0.0

This happens when the build runs under Python < 3.11. The `requires-python = ">= 3.11"` constraint in `pyproject.toml` causes setuptools to silently produce an empty package when the constraint is not met.

**Fix:** Always install inside a Python 3.11 virtual environment:

```bash
# Install Python 3.11 if needed (Ubuntu/Debian)
sudo apt install python3.11 python3.11-venv -y

# Create and activate a venv
python3.11 -m venv ~/.venvs/finops
source ~/.venvs/finops/bin/activate

# Confirm version
python --version   # must show 3.11.x or newer

# Install
pip install .
```

### `pip install -e .` fails with "build_editable hook" error

The build backend (`setuptools`) in this project does not support PEP 660 editable installs. Use `pip install .` instead. Re-run it after making code changes.

### `finops: command not found` after installing

The venv is not active. Run:

```bash
source ~/.venvs/finops/bin/activate
```

Add this to your `~/.bashrc` or `~/.zshrc` to activate automatically:

```bash
echo 'source ~/.venvs/finops/bin/activate' >> ~/.bashrc
```

### AWS Cost Explorer returns no data

Cost Explorer requires data to exist in your account. New accounts may have no
historical cost data. Wait 24 hours after first using AWS services, then re-run
`finops collect`.

Also confirm Cost Explorer is enabled:
> AWS Console → Billing → Cost Explorer → Enable

---

## Development

```bash
pip install ".[dev]"   # Install with dev dependencies

make lint              # ruff check
make typecheck         # mypy
make test              # pytest (skips integration tests)
make check             # all three
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

- [x] AWS Cost Explorer integration + Reserved Instance / Savings Plan tracking
- [x] AWS resource collection (EC2, EBS, RDS, S3, ELB, NAT Gateway, EKS, Lambda, VPN, CloudFront, API Gateway)
- [x] AWS resource cost estimation (pricing tables for EC2, EBS, ELB, NAT, EKS, RDS)
- [x] GCP BigQuery billing export integration
- [x] GCP resource collection (Compute VMs, Persistent Disks, LBs, GKE, Cloud SQL, GCS, Cloud Run, Cloud Functions, BigQuery, Pub/Sub)
- [x] Azure Cost Management + resource collection (VMs, Disks, LBs, AKS, Storage, App Service, SQL DB, Function Apps, VPN Gateways, CDN, Cosmos DB)
- [x] OCI Usage API + resource collection (Compute, Block Volumes, LBs, OKE, Autonomous DB, Object Storage)
- [x] Anomaly detection engine (cost spikes, new high-cost resources, scaling events)
- [x] Waste detection engine (unattached disks, stopped instances, stopped databases, idle NATs, unused EIPs)
- [x] Cost forecasting (linear regression + trend)
- [x] LLM-powered explanations (OpenAI, Anthropic, Amazon Bedrock, Groq, Gemini, Ollama)
- [x] CLI with table/JSON/plain output
- [x] Docker image with multi-stage build (all cloud providers included)
- [x] CloudWatch/Cloud Monitoring integration for CPU-based idle instance detection (AWS, Azure, GCP, OCI)
- [x] Dynamic pricing API integration (AWS Pricing API, Azure Retail Prices, GCP Cloud Billing Catalog) with in-memory cache and hardcoded fallback
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
