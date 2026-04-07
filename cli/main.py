# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import typer

from cli.config_loader import load_config
from cli.output import console, print_json, print_plain, print_table

if TYPE_CHECKING:
    from llm.client import LLMClient
    from storage.sqlite_adapter import SQLiteAdapter

app = typer.Typer(name="finops", help="Multi-cloud infrastructure cost reasoning agent.")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_storage(config: dict[str, Any]) -> SQLiteAdapter:
    import os
    from storage.sqlite_adapter import SQLiteAdapter as _SQLiteAdapter
    db_path = os.environ.get("FINOPS_DB_PATH") or config.get("storage", {}).get("path", "~/.finops-agent/finops.db")
    return _SQLiteAdapter(db_path)


def _get_llm_client(config: dict[str, Any]) -> LLMClient:
    from llm.client import LLMClient as _LLMClient
    llm_cfg = config.get("llm", {})
    return _LLMClient(
        provider=llm_cfg.get("provider", "openai"),
        api_key=llm_cfg.get("api_key", ""),
        model=llm_cfg.get("model", "gpt-4o"),
        base_url=llm_cfg.get("base_url", ""),
        bedrock_region=llm_cfg.get("bedrock_region", "us-east-1"),
    )


def _parse_since(since: str | None) -> date:
    if since:
        return date.fromisoformat(since)
    return date.today() - timedelta(days=30)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def summary(
    provider: str = typer.Option("aws", help="Cloud provider: aws|gcp|azure|oci|all"),
    output: str = typer.Option("table", help="Output format: json|table|plain"),
    since: str | None = typer.Option(None, help="Filter from date (YYYY-MM-DD)"),
    account_id: str | None = typer.Option(None, help="Filter by account/subscription/project ID"),
) -> None:
    """Total cost breakdown by provider/service/region."""
    config = load_config()
    storage = _get_storage(config)
    start = _parse_since(since)

    from intelligence.contributors import top_regions, top_services

    cost_history = storage.get_cost_history(provider, days=(date.today() - start).days, account_id=account_id)
    total = sum(cs.cost_usd for cs in cost_history)

    regions = top_regions(cost_history)
    services = top_services(cost_history)

    title_suffix = f" (account: {account_id})" if account_id else ""

    if output == "json":
        print_json({
            "total_cost_usd": round(total, 2),
            "account_id": account_id,
            "top_services": [asdict(s) for s in services],
            "top_regions": [asdict(r) for r in regions],
        })
    elif output == "plain":
        print_plain(f"Total cost{title_suffix}: ${total:,.2f}")
        for s in services:
            print_plain(f"  {s.name}: ${s.total_cost_usd:,.2f} ({s.percentage}%)")
    else:
        print_table(
            f"Cost Summary (since {start}){title_suffix}",
            ["Service", "Cost (USD)", "% of Total"],
            [[s.name, f"${s.total_cost_usd:,.2f}", f"{s.percentage}%"] for s in services],
        )
        print_table(
            "Top Regions",
            ["Region", "Cost (USD)", "% of Total"],
            [[r.name, f"${r.total_cost_usd:,.2f}", f"{r.percentage}%"] for r in regions],
        )
        console.print(f"\n[bold]Total: ${total:,.2f}[/bold]")


@app.command(name="explain-spike")
def explain_spike(
    provider: str = typer.Option("aws", help="Cloud provider"),
    output: str = typer.Option("table", help="Output format: json|table|plain"),
    account_id: str | None = typer.Option(None, help="Filter by account/subscription/project ID"),
) -> None:
    """Show anomalies with LLM explanation."""
    config = load_config()
    storage = _get_storage(config)

    from intelligence.anomaly import detect_cost_spikes

    cost_history = storage.get_cost_history(provider, days=14, account_id=account_id)
    anomalies = detect_cost_spikes(cost_history)

    if not anomalies:
        console.print("[green]No cost spikes detected.[/green]")
        return

    if output == "json":
        print_json([asdict(a) for a in anomalies])
        return

    if output != "plain":
        print_table(
            "Cost Spikes Detected",
            ["Resource", "Type", "Severity", "Detail"],
            [
                [a.resource_id, a.anomaly_type, a.severity, str(a.detail)]
                for a in anomalies
            ],
        )

    # LLM explanation
    llm_cfg = config.get("llm", {})
    if llm_cfg.get("api_key") or llm_cfg.get("provider") == "bedrock":
        from llm.prompt_builder import build_spike_prompt

        client = _get_llm_client(config)
        context = {"anomalies": [asdict(a) for a in anomalies], "provider": provider}
        system_prompt, user_prompt = build_spike_prompt(context)
        try:
            explanation = client.explain(system_prompt, user_prompt)
            console.print(f"\n[bold]Analysis:[/bold]\n{explanation}")
        except Exception as e:
            console.print(f"\n[red]LLM call failed: {e}[/red]")
    else:
        console.print("\n[dim]Set llm.api_key in config to get AI-powered explanations.[/dim]")


@app.command(name="top-cost")
def top_cost(
    provider: str = typer.Option("aws", help="Cloud provider"),
    output: str = typer.Option("table", help="Output format: json|table|plain"),
    account_id: str | None = typer.Option(None, help="Filter by account/subscription/project ID"),
) -> None:
    """Top 10 most expensive resources."""
    config = load_config()
    storage = _get_storage(config)

    from intelligence.contributors import top_resources

    resources = storage.get_resource_snapshots(provider, account_id=account_id)
    top = top_resources(resources)

    if output == "json":
        print_json([asdict(t) for t in top])
    elif output == "plain":
        for t in top:
            print_plain(
                f"{t.name} ({t.service}, {t.region}): "
                f"${t.total_cost_usd:,.2f}/day ({t.percentage}%)  "
                f"ID: {t.resource_id}"
            )
    else:
        print_table(
            "Top 10 Most Expensive Resources",
            ["Resource", "Resource ID / ARN", "Service", "Region", "State", "Daily Cost (USD)", "% of Total"],
            [
                [
                    t.name,
                    t.resource_id,
                    t.service,
                    t.region,
                    t.state,
                    f"${t.total_cost_usd:,.2f}",
                    f"{t.percentage}%",
                ]
                for t in top
            ],
        )


@app.command(name="find-waste")
def find_waste(
    provider: str = typer.Option("aws", help="Cloud provider"),
    output: str = typer.Option("table", help="Output format: json|table|plain"),
    account_id: str | None = typer.Option(None, help="Filter by account/subscription/project ID"),
) -> None:
    """List waste findings with estimated savings."""
    config = load_config()
    storage = _get_storage(config)

    from intelligence.waste import find_all_waste

    resources = storage.get_resource_snapshots(provider, account_id=account_id)
    findings = find_all_waste(resources)

    if not findings:
        console.print("[green]No waste detected.[/green]")
        return

    total_savings = sum(f.estimated_monthly_savings for f in findings)

    if output == "json":
        print_json([asdict(f) for f in findings])
    elif output == "plain":
        for f in findings:
            savings = f.estimated_monthly_savings
            print_plain(f"[{f.waste_type}] {f.description} (saves ~${savings}/mo)")
        print_plain(f"\nTotal estimated savings: ${total_savings:,.2f}/month")
    else:
        print_table(
            "Waste Findings",
            ["Type", "Resource ID / ARN", "Service", "Region", "Description", "Est. Savings/Mo"],
            [
                [
                    f.waste_type, f.resource_id, f.service,
                    f.region, f.description, f"${f.estimated_monthly_savings:,.2f}",
                ]
                for f in findings
            ],
        )
        console.print(f"\n[bold]Total estimated savings: ${total_savings:,.2f}/month[/bold]")


@app.command()
def forecast(
    provider: str = typer.Option("aws", help="Cloud provider"),
    output: str = typer.Option("table", help="Output format: json|table|plain"),
    account_id: str | None = typer.Option(None, help="Filter by account/subscription/project ID"),
) -> None:
    """Show projected monthly cost."""
    config = load_config()
    storage = _get_storage(config)

    from intelligence.forecast import compute_forecast

    cost_history = storage.get_cost_history(provider, days=30, account_id=account_id)
    results = compute_forecast(cost_history)

    if not results:
        console.print("[yellow]Not enough data to forecast. Run 'finops collect' first.[/yellow]")
        return

    if output == "json":
        print_json([asdict(r) for r in results])
    elif output == "plain":
        for r in results:
            print_plain(
                f"{r.provider} ({r.period}): "
                f"${r.projected_monthly_cost:,.2f}/month projected, "
                f"trend {r.trend_direction} (${r.avg_daily_cost:,.2f}/day avg)"
            )
    else:
        print_table(
            "Cost Forecast",
            ["Provider", "Period", "Avg Daily", "Projected Monthly", "Trend"],
            [
                [
                    r.provider,
                    r.period,
                    f"${r.avg_daily_cost:,.2f}",
                    f"${r.projected_monthly_cost:,.2f}",
                    r.trend_direction,
                ]
                for r in results
            ],
        )


@app.command(name="explain-bill")
def explain_bill(
    provider: str = typer.Option("aws", help="Cloud provider"),
    since: str | None = typer.Option(None, help="Filter from date (YYYY-MM-DD)"),
    account_id: str | None = typer.Option(None, help="Filter by account/subscription/project ID"),
) -> None:
    """Full bill breakdown with LLM-powered reasoning."""
    config = load_config()
    storage = _get_storage(config)
    start = _parse_since(since)

    from intelligence.anomaly import detect_cost_spikes
    from intelligence.contributors import top_resources, top_services
    from intelligence.waste import find_all_waste

    days = (date.today() - start).days
    cost_history = storage.get_cost_history(provider, days=days, account_id=account_id)
    resources = storage.get_resource_snapshots(provider, account_id=account_id)

    total_cost = sum(cs.cost_usd for cs in cost_history)
    services = top_services(cost_history)
    top_res = top_resources(resources)
    anomalies = detect_cost_spikes(cost_history)
    waste = find_all_waste(resources)

    context = {
        "provider": provider,
        "period": f"{start} to {date.today()}",
        "total_cost_usd": round(total_cost, 2),
        "top_services": [asdict(s) for s in services],
        "top_resources": [asdict(r) for r in top_res],
        "anomalies": [asdict(a) for a in anomalies],
        "waste": [asdict(w) for w in waste],
    }

    llm_cfg = config.get("llm", {})
    if llm_cfg.get("api_key") or llm_cfg.get("provider") == "bedrock":
        from llm.prompt_builder import build_bill_prompt

        client = _get_llm_client(config)
        system_prompt, user_prompt = build_bill_prompt(context)
        try:
            explanation = client.explain(system_prompt, user_prompt)
            console.print(explanation)
            return
        except Exception as e:
            console.print(f"[red]LLM call failed: {e}[/red]\n")

    # Fallback: print structured data without LLM
    console.print(f"[bold]Bill Summary ({start} to {date.today()})[/bold]")
    console.print(f"Total: ${total_cost:,.2f}\n")
    console.print("[bold]Top Services:[/bold]")
    for s in services:
        console.print(f"  {s.name}: ${s.total_cost_usd:,.2f} ({s.percentage}%)")
    if top_res:
        console.print("\n[bold]Top Resources:[/bold]")
        for r in top_res:
            console.print(
                f"  {r.name} [{r.service}, {r.region}]: "
                f"${r.total_cost_usd:,.2f}/day ({r.percentage}%)"
            )
            console.print(f"    ID: {r.resource_id}")
    if anomalies:
        console.print(f"\n[bold]Anomalies: {len(anomalies)} detected[/bold]")
    if waste:
        savings = sum(w.estimated_monthly_savings for w in waste)
        msg = f"Waste: {len(waste)} findings, ~${savings:,.2f}/month savings"
        console.print(f"\n[bold]{msg}[/bold]")


def _collect_for_provider(
    provider_name: str,
    accounts: list[dict[str, Any]],
    storage: SQLiteAdapter,
) -> None:
    """Run cost + resource collection for every account of a single provider."""
    start = date.today() - timedelta(days=30)
    end = date.today()

    for acct in accounts:
        acct_label = acct.get("name", "default")
        console.print(f"\n[bold]— {provider_name.upper()} account: {acct_label}[/bold]")

        try:
            collector = _build_collector(provider_name, acct)
        except Exception as e:
            console.print(f"[red]Failed to initialise collector: {e}[/red]")
            continue

        if not collector.test_connection():
            console.print(f"[red]{provider_name.upper()} connection failed for '{acct_label}'. Check credentials.[/red]")
            continue

        console.print("  Collecting cost data (last 30 days)...")
        try:
            costs = collector.collect_costs(start, end)
            storage.save_cost_snapshots(costs)
            console.print(f"    Saved {len(costs)} cost snapshots.")
        except Exception as e:
            console.print(f"  [yellow]Cost collection failed: {e}[/yellow]")

        console.print("  Collecting resource data...")
        try:
            resources = collector.collect_resources()
            storage.save_resource_snapshots(resources)
            console.print(f"    Saved {len(resources)} resource snapshots.")
        except Exception as e:
            console.print(f"  [red]Resource collection failed: {e}[/red]")

        console.print(f"  [green]{provider_name.upper()} account '{acct_label}' done.[/green]")


def _build_collector(provider_name: str, acct: dict[str, Any]) -> Any:
    """Instantiate the right collector for a provider + account config dict."""
    if provider_name == "aws":
        from cloud.aws.collector import AWSCollector
        return AWSCollector(
            profile=acct.get("profile"),
            access_key_id=acct.get("access_key_id") or None,
            secret_access_key=acct.get("secret_access_key") or None,
            regions=acct.get("regions", ["us-east-1"]),
        )
    if provider_name == "gcp":
        from cloud.gcp.collector import GCPCollector
        return GCPCollector(
            project_id=acct.get("project_id", ""),
            credentials_file=acct.get("credentials_file") or None,
            billing_project_id=acct.get("billing_project_id") or None,
            billing_dataset=acct.get("billing_dataset") or None,
            billing_table=acct.get("billing_table") or None,
        )
    if provider_name == "azure":
        from cloud.azure.collector import AzureCollector
        return AzureCollector(
            subscription_id=acct.get("subscription_id", ""),
            tenant_id=acct.get("tenant_id") or None,
            client_id=acct.get("client_id") or None,
            client_secret=acct.get("client_secret") or None,
        )
    if provider_name == "oci":
        from cloud.oci.collector import OCICollector
        return OCICollector(
            compartment_id=acct.get("compartment_id", ""),
            tenancy_id=acct.get("tenancy_id") or None,
            config_file=acct.get("config_file") or None,
            profile=acct.get("profile") or None,
        )
    raise ValueError(f"Unknown provider: {provider_name}")


@app.command()
def collect(
    provider: str = typer.Option("aws", help="Cloud provider: aws|gcp|azure|oci|all"),
) -> None:
    """Manually trigger a data collection run (supports multiple accounts per provider)."""
    config = load_config()
    storage = _get_storage(config)

    providers = ["aws", "gcp", "azure", "oci"] if provider == "all" else [provider]

    for prov in providers:
        prov_cfg = config.get(prov, {})
        if not prov_cfg.get("enabled", False):
            console.print(f"[yellow]{prov.upper()} is not enabled in config.[/yellow]")
            continue

        accounts: list[dict[str, Any]] = prov_cfg.get("accounts", [])
        if not accounts:
            console.print(f"[yellow]No accounts configured for {prov.upper()}.[/yellow]")
            continue

        console.print(f"\n[bold]=== {prov.upper()}: {len(accounts)} account(s) ===[/bold]")
        _collect_for_provider(prov, accounts, storage)

    console.print("\n[green bold]Collection complete.[/green bold]")


@app.command()
def config(
    action: str = typer.Argument(help="Action: set|get|path"),
    key: str | None = typer.Argument(None, help="Config key (dot-separated, e.g. llm.api_key)"),
    value: str | None = typer.Argument(None, help="Value to set"),
) -> None:
    """Manage configuration."""
    import os
    from pathlib import Path

    import yaml as _yaml

    config_path = Path(os.path.expanduser("~/.finops-agent/config.yaml"))

    if action == "path":
        console.print(str(config_path))
        return

    if action == "get":
        if not config_path.exists():
            console.print("[yellow]No config file found.[/yellow]")
            return
        cfg: dict[str, Any] = load_config(str(config_path))
        if key:
            parts = key.split(".")
            val: Any = cfg
            for p in parts:
                if isinstance(val, dict):
                    val = val.get(p)
                else:
                    val = None
                    break
            console.print(str(val))
        else:
            console.print(_yaml.dump(cfg, default_flow_style=False))
        return

    if action == "set":
        if not key or value is None:
            console.print("[red]Usage: finops config set <key> <value>[/red]")
            raise typer.Exit(1)

        config_path.parent.mkdir(parents=True, exist_ok=True)
        set_cfg: dict[str, Any] = {}
        if config_path.exists():
            with open(config_path) as f:
                set_cfg = _yaml.safe_load(f) or {}

        parts = key.split(".")
        target = set_cfg
        for p in parts[:-1]:
            if p not in target or not isinstance(target[p], dict):
                target[p] = {}
            target = target[p]
        # Parse JSON/YAML values so lists and booleans are stored correctly.
        # e.g. '["us-east-1"]' → list, 'true' → bool, '42' → int
        parsed_value: Any = value
        try:
            parsed_value = _yaml.safe_load(value)
        except Exception:
            pass
        # If parsing returned None for a non-empty string, keep the string
        if parsed_value is None and value:
            parsed_value = value
        target[parts[-1]] = parsed_value

        with open(config_path, "w") as f:
            _yaml.dump(set_cfg, f, default_flow_style=False)

        os.chmod(config_path, 0o600)
        console.print(f"[green]Set {key} = {value}[/green]")
        return

    console.print(f"[red]Unknown action: {action}. Use set, get, or path.[/red]")


if __name__ == "__main__":
    app()
