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
    from storage.sqlite_adapter import SQLiteAdapter as _SQLiteAdapter
    return _SQLiteAdapter(config.get("storage", {}).get("path", "~/.finops-agent/finops.db"))


def _get_llm_client(config: dict[str, Any]) -> LLMClient:
    from llm.client import LLMClient as _LLMClient
    llm_cfg = config.get("llm", {})
    return _LLMClient(
        provider=llm_cfg.get("provider", "openai"),
        api_key=llm_cfg.get("api_key", ""),
        model=llm_cfg.get("model", "gpt-4o"),
        base_url=llm_cfg.get("base_url", ""),
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
    provider: str = typer.Option("aws", help="Cloud provider: aws|gcp|azure|all"),
    output: str = typer.Option("table", help="Output format: json|table|plain"),
    since: str | None = typer.Option(None, help="Filter from date (YYYY-MM-DD)"),
) -> None:
    """Total cost breakdown by provider/service/region."""
    config = load_config()
    storage = _get_storage(config)
    start = _parse_since(since)

    from intelligence.contributors import top_regions, top_services

    cost_history = storage.get_cost_history(provider, days=(date.today() - start).days)
    total = sum(cs.cost_usd for cs in cost_history)

    regions = top_regions(cost_history)
    services = top_services(cost_history)

    if output == "json":
        print_json({
            "total_cost_usd": round(total, 2),
            "top_services": [asdict(s) for s in services],
            "top_regions": [asdict(r) for r in regions],
        })
    elif output == "plain":
        print_plain(f"Total cost: ${total:,.2f}")
        for s in services:
            print_plain(f"  {s.name}: ${s.total_cost_usd:,.2f} ({s.percentage}%)")
    else:
        print_table(
            f"Cost Summary (since {start})",
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
) -> None:
    """Show anomalies with LLM explanation."""
    config = load_config()
    storage = _get_storage(config)

    from intelligence.anomaly import detect_cost_spikes

    cost_history = storage.get_cost_history(provider, days=14)
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
    if llm_cfg.get("api_key"):
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
) -> None:
    """Top 10 most expensive resources."""
    config = load_config()
    storage = _get_storage(config)

    from intelligence.contributors import top_resources

    resources = storage.get_resource_snapshots(provider)
    top = top_resources(resources)

    if output == "json":
        print_json([asdict(t) for t in top])
    elif output == "plain":
        for t in top:
            print_plain(f"{t.name}: ${t.total_cost_usd:,.2f}/day ({t.percentage}%)")
    else:
        print_table(
            "Top 10 Most Expensive Resources",
            ["Resource", "Daily Cost (USD)", "% of Total"],
            [[t.name, f"${t.total_cost_usd:,.2f}", f"{t.percentage}%"] for t in top],
        )


@app.command(name="find-waste")
def find_waste(
    provider: str = typer.Option("aws", help="Cloud provider"),
    output: str = typer.Option("table", help="Output format: json|table|plain"),
) -> None:
    """List waste findings with estimated savings."""
    config = load_config()
    storage = _get_storage(config)

    from intelligence.waste import find_all_waste

    resources = storage.get_resource_snapshots(provider)
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
            ["Type", "Resource", "Service", "Region", "Est. Savings/Mo"],
            [
                [
                    f.waste_type, f.resource_id, f.service,
                    f.region, f"${f.estimated_monthly_savings:,.2f}",
                ]
                for f in findings
            ],
        )
        console.print(f"\n[bold]Total estimated savings: ${total_savings:,.2f}/month[/bold]")


@app.command()
def forecast(
    provider: str = typer.Option("aws", help="Cloud provider"),
    output: str = typer.Option("table", help="Output format: json|table|plain"),
) -> None:
    """Show projected monthly cost."""
    config = load_config()
    storage = _get_storage(config)

    from intelligence.forecast import compute_forecast

    cost_history = storage.get_cost_history(provider, days=30)
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
) -> None:
    """Full bill breakdown with LLM-powered reasoning."""
    config = load_config()
    storage = _get_storage(config)
    start = _parse_since(since)

    from intelligence.anomaly import detect_cost_spikes
    from intelligence.contributors import top_resources, top_services
    from intelligence.waste import find_all_waste

    days = (date.today() - start).days
    cost_history = storage.get_cost_history(provider, days=days)
    resources = storage.get_resource_snapshots(provider)

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
    if llm_cfg.get("api_key"):
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
    if anomalies:
        console.print(f"\n[bold]Anomalies: {len(anomalies)} detected[/bold]")
    if waste:
        savings = sum(w.estimated_monthly_savings for w in waste)
        msg = f"Waste: {len(waste)} findings, ~${savings:,.2f}/month savings"
        console.print(f"\n[bold]{msg}[/bold]")


@app.command()
def collect(
    provider: str = typer.Option("aws", help="Cloud provider"),
) -> None:
    """Manually trigger a data collection run."""
    config = load_config()
    storage = _get_storage(config)

    if provider in ("aws", "all"):
        aws_cfg = config.get("aws", {})
        if not aws_cfg.get("enabled", False):
            console.print("[yellow]AWS is not enabled in config.[/yellow]")
            return

        from cloud.aws.collector import AWSCollector

        console.print("Connecting to AWS...")
        collector = AWSCollector(
            profile=aws_cfg.get("profile"),
            access_key_id=aws_cfg.get("access_key_id") or None,
            secret_access_key=aws_cfg.get("secret_access_key") or None,
            regions=aws_cfg.get("regions", ["us-east-1"]),
        )

        if not collector.test_connection():
            console.print("[red]AWS connection failed. Check credentials.[/red]")
            return

        console.print("Collecting cost data (last 30 days)...")
        start = date.today() - timedelta(days=30)
        end = date.today()
        costs = collector.collect_costs(start, end)
        storage.save_cost_snapshots(costs)
        console.print(f"  Saved {len(costs)} cost snapshots.")

        console.print("Collecting resource data...")
        resources = collector.collect_resources()
        storage.save_resource_snapshots(resources)
        console.print(f"  Saved {len(resources)} resource snapshots.")

        console.print("[green]AWS collection complete.[/green]")

    if provider in ("gcp", "all"):
        gcp_cfg = config.get("gcp", {})
        if not gcp_cfg.get("enabled", False):
            console.print("[yellow]GCP is not enabled in config.[/yellow]")
        else:
            from cloud.gcp.collector import GCPCollector

            console.print("Connecting to GCP...")
            gcp_collector = GCPCollector(
                project_id=gcp_cfg.get("project_id", ""),
                credentials_file=gcp_cfg.get("credentials_file") or None,
                billing_project_id=gcp_cfg.get("billing_project_id") or None,
                billing_dataset=gcp_cfg.get("billing_dataset") or None,
                billing_table=gcp_cfg.get("billing_table") or None,
            )

            if not gcp_collector.test_connection():
                console.print("[red]GCP connection failed. Check credentials.[/red]")
            else:
                console.print("Collecting GCP cost data (last 30 days)...")
                start = date.today() - timedelta(days=30)
                end = date.today()
                gcp_costs = gcp_collector.collect_costs(start, end)
                storage.save_cost_snapshots(gcp_costs)
                console.print(f"  Saved {len(gcp_costs)} GCP cost snapshots.")

                console.print("Collecting GCP resource data...")
                gcp_resources = gcp_collector.collect_resources()
                storage.save_resource_snapshots(gcp_resources)
                console.print(f"  Saved {len(gcp_resources)} GCP resource snapshots.")

                console.print("[green]GCP collection complete.[/green]")

    if provider in ("azure", "all"):
        azure_cfg = config.get("azure", {})
        if not azure_cfg.get("enabled", False):
            console.print("[yellow]Azure is not enabled in config.[/yellow]")
        else:
            from cloud.azure.collector import AzureCollector

            console.print("Connecting to Azure...")
            azure_collector = AzureCollector(
                subscription_id=azure_cfg.get("subscription_id", ""),
                tenant_id=azure_cfg.get("tenant_id") or None,
                client_id=azure_cfg.get("client_id") or None,
                client_secret=azure_cfg.get("client_secret") or None,
            )

            if not azure_collector.test_connection():
                console.print("[red]Azure connection failed. Check credentials.[/red]")
            else:
                console.print("Collecting Azure cost data (last 30 days)...")
                start = date.today() - timedelta(days=30)
                end = date.today()
                azure_costs = azure_collector.collect_costs(start, end)
                storage.save_cost_snapshots(azure_costs)
                console.print(f"  Saved {len(azure_costs)} Azure cost snapshots.")

                console.print("Collecting Azure resource data...")
                azure_resources = azure_collector.collect_resources()
                storage.save_resource_snapshots(azure_resources)
                console.print(f"  Saved {len(azure_resources)} Azure resource snapshots.")

                console.print("[green]Azure collection complete.[/green]")


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
        target[parts[-1]] = value

        with open(config_path, "w") as f:
            _yaml.dump(set_cfg, f, default_flow_style=False)

        os.chmod(config_path, 0o600)
        console.print(f"[green]Set {key} = {value}[/green]")
        return

    console.print(f"[red]Unknown action: {action}. Use set, get, or path.[/red]")


if __name__ == "__main__":
    app()
