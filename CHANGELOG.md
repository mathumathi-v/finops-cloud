# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.2] - 2026-03-22

### Added
- Amazon Bedrock LLM provider — supports Nova, Titan, Claude, Llama, Mistral via Converse API
- Bedrock uses IAM credentials (no separate API key required)
- `llm.bedrock_region` config option (defaults to `us-east-1`)
- 7 unit tests for Bedrock provider (converse API, edge cases, region config)
- Updated README with Bedrock setup guide, architecture diagram, and config reference

## [0.3.0] - 2026-03-20

### Added
- Dockerfile with multi-stage build (Python 3.12-slim, all cloud SDKs included)
- `.dockerignore` for optimized build context
- Docker Hub image: `mathumathi247/finops-agent:latest`
- `docker-build` and `docker-run` Makefile targets
- Docker usage instructions in README for all cloud providers (AWS, GCP, Azure, OCI)

### Removed
- `blog/` directory

## [0.2.0] - 2026-03-18

### Added
- Azure Cost Management collector (daily cost per service/region)
- Azure resource collector: VMs, Managed Disks, Load Balancers, AKS, Storage Accounts, App Service Plans
- OCI Usage API collector (daily cost per service/region)
- OCI resource collector: Compute instances, Block Volumes, Load Balancers, OKE clusters

## [0.1.0] - 2026-03-16

### Added
- Cost model dataclasses: `ResourceSnapshot`, `CostSnapshot`, `AnomalyEvent`
- SQLite storage adapter with full CRUD and schema initialisation
- AWS Cost Explorer collector (daily cost per service/region, 30-day window)
- AWS resource collector: EC2, EBS, ELB/ALB, NAT Gateway, EKS clusters and node groups
- GCP BigQuery billing export collector (daily cost per service/region)
- GCP resource collector: Compute Engine VMs, Persistent Disks, Load Balancers (global + regional), GKE clusters and node pools
- Azure Cost Management collector (daily cost per service/region)
- Azure resource collector: Virtual Machines, Managed Disks, Load Balancers, AKS clusters and node pools
- Intelligence engine — anomaly detection: cost spikes (>1.25x), new high-cost resources (>$50/day), sudden scaling (>2x)
- Intelligence engine — waste detection: unattached disks (AWS/GCP/Azure), stopped instances, idle NAT gateways, unused Elastic IPs
- Intelligence engine — forecasting: linear regression over 14 days, monthly projection, trend direction
- Intelligence engine — contributor analysis: top services, regions, resources by spend
- LLM client supporting OpenAI, Anthropic, and any OpenAI-compatible endpoint (Groq, Gemini, Ollama)
- Prompt injection guard: cloud-sourced strings sanitized before LLM; account IDs, ARNs, IPs, hostnames redacted
- Prompt payload truncation to 8000 chars to stay within free-tier LLM token limits
- CLI commands: `collect`, `summary`, `explain-bill`, `explain-spike`, `top-cost`, `find-waste`, `forecast`, `config`
- `--output json|table|plain` flag on all commands
- Config file permission enforcement (refuses to run if config is world-readable)
- 80 unit tests across all modules; integration tests gated behind `@pytest.mark.integration`
- Apache-2.0 license, SECURITY.md, CHANGELOG.md
