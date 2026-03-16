# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | Yes                |

## Reporting a Vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please use [GitHub Security Advisories](https://github.com/your-org/finops-agent/security/advisories/new) to report vulnerabilities privately.

### Expected Response Time

- **Acknowledgement**: within 48 hours
- **Patch for critical issues**: within 14 days

### In Scope

- Credential handling and storage
- Data exposure (cost data, account IDs, resource identifiers)
- Read-only enforcement violations (any code path that could trigger a write/mutate cloud API call)
- SQLite injection or data leakage
- LLM prompt injection via cloud-sourced data

### Out of Scope

- Issues caused by user-misconfigured IAM policies
- Vulnerabilities in upstream dependencies (report those to the upstream project)
- Denial of service against the local CLI process
