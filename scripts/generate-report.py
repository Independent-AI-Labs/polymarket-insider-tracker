#!/usr/bin/env python3
"""Fetch Polymarket data from Gamma API and generate a markdown report."""

import argparse
import json
import urllib.request
from datetime import datetime
from pathlib import Path

GAMMA_API = "https://gamma-api.polymarket.com"


def fetch_json(url: str) -> list | dict:
    req = urllib.request.Request(url, headers={"User-Agent": "AMI-Reports/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fmt_usd(val: float) -> str:
    if val >= 1_000_000:
        return f"${val / 1_000_000:.2f}M"
    if val >= 1_000:
        return f"${val / 1_000:.1f}K"
    return f"${val:,.0f}"


def market_row(i: int, m: dict) -> str:
    q = m.get("question", "N/A")
    if len(q) > 65:
        q = q[:62] + "..."
    vol24 = fmt_usd(float(m.get("volume24hr", 0) or 0))
    vol_total = fmt_usd(float(m.get("volume", 0) or 0))
    liq = fmt_usd(float(m.get("liquidityClob", 0) or 0))
    bid = m.get("bestBid", "—")
    ask = m.get("bestAsk", "—")
    end = (m.get("endDate") or "—")[:10]
    return f"| {i} | {q} | {vol24} | {vol_total} | {liq} | {bid}/{ask} | {end} |"


TABLE_HEADER = (
    "| # | Question | 24h Vol | Total Vol | Liquidity | Bid/Ask | End Date |\n"
    "|---|----------|---------|-----------|-----------|---------|----------|"
)


def generate_report(date: str) -> str:
    sections = [
        ("Top 20 Markets by 24-Hour Volume", "volume24hr", "false"),
        ("Top 20 Markets by Liquidity", "liquidityClob", "false"),
        ("Recently Created Markets", "startDate", "false"),
    ]

    all_data: dict[str, list] = {}
    for title, order, asc in sections:
        url = f"{GAMMA_API}/markets?limit=20&order={order}&ascending={asc}&active=true"
        all_data[title] = fetch_json(url)

    # Summary stats from volume section
    vol_markets = all_data[sections[0][0]]
    total_24h = sum(float(m.get("volume24hr", 0) or 0) for m in vol_markets)
    total_vol = sum(float(m.get("volume", 0) or 0) for m in vol_markets)

    liq_markets = all_data[sections[1][0]]
    total_liq = sum(float(m.get("liquidityClob", 0) or 0) for m in liq_markets)

    lines = [
        f"# Polymarket Market Snapshot — {date}",
        f"",
        f"*Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC*",
        f"",
        f"## Summary",
        f"",
        f"- **Top-20 24h volume total**: {fmt_usd(total_24h)}",
        f"- **Top-20 all-time volume total**: {fmt_usd(total_vol)}",
        f"- **Top-20 liquidity total**: {fmt_usd(total_liq)}",
        f"",
    ]

    for i, (title, _, _) in enumerate(sections, 1):
        markets = all_data[title]
        lines.append(f"## {i}. {title}")
        lines.append("")
        lines.append(TABLE_HEADER)
        for j, m in enumerate(markets, 1):
            lines.append(market_row(j, m))
        lines.append("")

    # Observations
    lines.append("## Notable Observations")
    lines.append("")

    # Find high volume/liquidity ratios
    for m in vol_markets[:10]:
        vol = float(m.get("volume24hr", 0) or 0)
        liq = float(m.get("liquidityClob", 0) or 0)
        if liq > 0 and vol / liq > 8:
            q = m.get("question", "?")[:60]
            lines.append(
                f'- **Thin book**: "{q}" — {fmt_usd(vol)} 24h vol vs {fmt_usd(liq)} liquidity '
                f"({vol / liq:.0f}x ratio)"
            )

    # Find near-certain non-sports markets
    for m in vol_markets[:10]:
        bid = float(m.get("bestBid", 0.5) or 0.5)
        vol = float(m.get("volume24hr", 0) or 0)
        q = m.get("question", "")
        # Skip sports
        if any(kw in q.lower() for kw in ["vs.", "o/u", "spread"]):
            continue
        if bid > 0.92 or bid < 0.05:
            lines.append(
                f'- **Near-certain ({bid:.3f})**: "{q[:60]}" — {fmt_usd(vol)} 24h vol'
            )

    lines.append("")
    lines.append("---")
    lines.append("*Data source: Polymarket Gamma API*")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.utcnow().strftime("%Y-%m-%d"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    report = generate_report(args.date)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(report)
    print(f"Report generated: {args.output}")


if __name__ == "__main__":
    main()
