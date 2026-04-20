"""Phase S3 — flagged-activity-log PDF.

Per docs/IMPLEMENTATION-PLAN-SIGNALS.md Phase S3. Takes the same
DailyReport the newsletter uses, emits a standalone analyst
appendix as PDF:

1. Cover strip — date, window, headline stats.
2. Flagged-activity log — every signal hit, grouped by market,
   descending by combined flagged notional on that market. Each
   market gets a heading + a table of wallets / sides / sizes /
   timestamps / signals-triggered.
3. Top markets — rank of markets by flagged notional (not all-24h
   volume, which is the legacy snapshot's content — this is
   flagged-only).
4. Signal glossary (compact).

No duplication with the email body: the body shows *summary*
sections (top 5 per signal). The PDF is the full audit trail.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from .signals import DailyReport

LOG = logging.getLogger(__name__)

CSS = """
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    font-size: 9.5pt; color: #111; line-height: 1.4;
  }
  h1 { font-size: 18pt; margin: 0 0 4pt; }
  h2 { font-size: 12pt; margin: 14pt 0 4pt; color: #111;
       border-bottom: 1px solid #ddd; padding-bottom: 2pt; }
  h3 { font-size: 10pt; margin: 10pt 0 3pt; color: #222; font-weight: 600; }
  p  { margin: 2pt 0; }
  .sub { color: #666; font-size: 9pt; }
  .muted { color: #888; font-size: 8.5pt; }
  a { color: #1a5fb4; text-decoration: none; }
  table { width: 100%; border-collapse: collapse; margin: 4pt 0 8pt;
          font-size: 8.5pt; }
  th, td { padding: 3pt 5pt; border-bottom: 1px solid #eee;
           text-align: left; vertical-align: top; }
  th { background: #f5f5f5; font-weight: 600; color: #333; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.mono { font-family: 'Menlo', monospace; font-size: 8pt; }
  td.tag { font-size: 8pt; color: #555; }
  thead { display: table-header-group; }
  tr, th, td { page-break-inside: avoid; }
  .cover { background: #1a1a2e; color: #fff; padding: 18pt 20pt;
           margin: 0 0 14pt; border-radius: 4pt; }
  .cover .eyebrow { color: #aaa; text-transform: uppercase;
                    letter-spacing: 0.1em; font-size: 8.5pt; }
  .cover h1 { margin: 4pt 0 0; color: #fff; }
  .cover p  { color: #aaa; margin: 4pt 0 0; font-size: 9pt; }
  .kpi { display: table; margin: 10pt 0 0; font-size: 9pt; }
  .kpi .row { display: table-row; }
  .kpi .cell { display: table-cell; padding-right: 16pt; color: #aaa; }
  .kpi .val  { display: table-cell; padding-right: 16pt; color: #fff;
               font-weight: 700; font-size: 11pt; }
  .signal-tag { display: inline-block; background: #eef3ff;
                color: #1a5fb4; padding: 1pt 5pt; border-radius: 3pt;
                font-size: 8pt; margin-right: 3pt; }
"""


def render_pdf(report: DailyReport, out_path: Path) -> Path:
    """Build the Option-B PDF and return the path written."""
    html = _render_html(report)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", prefix="appendix-", delete=False
    ) as fh:
        fh.write(html)
        html_path = Path(fh.name)

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "wkhtmltopdf",
            "--quiet",
            "--enable-local-file-access",
            "--margin-top", "8mm",
            "--margin-bottom", "10mm",
            "--margin-left", "8mm",
            "--margin-right", "8mm",
            "--page-size", "A4",
            "--encoding", "utf-8",
            "--footer-center", "[page]/[topage]",
            "--footer-font-size", "7",
            "--footer-spacing", "3",
            str(html_path),
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"wkhtmltopdf failed (rc={result.returncode}): "
                f"{result.stderr or result.stdout}"
            )
    finally:
        html_path.unlink(missing_ok=True)
    return out_path


def _render_html(report: DailyReport) -> str:
    parts: list[str] = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<style>{CSS}</style></head><body>",
    ]
    parts.append(_cover(report))
    parts.append(_headline(report))
    parts.append(_by_market_log(report))
    parts.append(_top_markets_by_flagged(report))
    parts.append(_glossary(report))
    parts.append(_footer(report))
    parts.append("</body></html>")
    return "\n".join(parts)


def _cover(report: DailyReport) -> str:
    summary_html = "".join(
        f'<div class="row"><div class="cell">{label}</div>'
        f'<div class="val">{value}</div></div>'
        for label, value in report.summary
    )
    window_fmt = (
        f"{report.window_start:%Y-%m-%d %H:%M} → "
        f"{report.window_end:%Y-%m-%d %H:%M} UTC "
        f"({(report.window_end - report.window_start).total_seconds() / 3600:.1f} h)"
    )
    return f"""
      <div class="cover">
        <div class="eyebrow">AMI Reports · Polymarket activity log</div>
        <h1>{report.date}</h1>
        <p>Window: {window_fmt}</p>
        <p>Edition: {report.edition_id} · Source: {report.source_label}</p>
        <div class="kpi">{summary_html}</div>
      </div>
    """


def _headline(report: DailyReport) -> str:
    return f"""
      <p style="font-size:11pt;line-height:1.5;margin:0 0 14pt">
        {report.headline}
      </p>
    """


def _by_market_log(report: DailyReport) -> str:
    """The core value of the PDF — every hit, grouped by market.

    A market that appears in multiple signal sections shows up
    ONCE with all its hits consolidated + every signal tag listed.
    """
    by_market: dict[str, dict[str, Any]] = {}
    for section in report.sections:
        signal_id = section.signal_id
        signal_name = section.title
        for row in section.rows:
            # Rows from market-level signals have market_title in the
            # row but empty wallet; wallet-level signals have both.
            mkey = row.get("market_url") or row.get("market_title", "")
            if not mkey:
                continue
            entry = by_market.setdefault(
                mkey,
                {
                    "title": row.get("market_title", ""),
                    "url": row.get("market_url", ""),
                    "signals": set(),
                    "rows": [],
                    "total_notional": 0.0,
                },
            )
            entry["signals"].add(signal_name)
            notional = (
                row.get("notional")
                or row.get("net_notional")
                or row.get("combined_notional")
                or row.get("volume_24h")
                or 0
            )
            entry["total_notional"] += float(abs(notional))
            entry["rows"].append(
                {
                    "signal": signal_name,
                    "wallet": row.get("wallet_display", ""),
                    "wallet_url": row.get("wallet_url", ""),
                    "side": row.get("side", ""),
                    "notional_fmt": _row_notional(row),
                    "extra": _row_extra(row, signal_id),
                }
            )

    # Sort markets by aggregate flagged notional.
    ordered = sorted(
        by_market.items(), key=lambda kv: kv[1]["total_notional"], reverse=True
    )

    parts = ["<h2>Flagged-activity log (by market)</h2>"]
    parts.append(
        '<p class="sub">Every signal hit from the signal registry, '
        'consolidated per market. A market listed here fired at '
        'least one detector during the window. Signals active are '
        'shown as tags next to the market title.</p>'
    )
    if not ordered:
        parts.append('<p class="muted">No markets crossed any signal threshold this window.</p>')
        return "".join(parts)

    for _, e in ordered:
        tags_html = "".join(f'<span class="signal-tag">{s}</span>' for s in sorted(e["signals"]))
        title_html = (
            f'<a href="{e["url"]}">{e["title"]}</a>' if e["url"] else e["title"]
        )
        parts.append(
            f'<h3>{title_html} {tags_html}</h3>'
        )
        parts.append(
            '<table><thead><tr>'
            '<th>Signal</th><th>Wallet</th><th>Side</th>'
            '<th class="num">Notional / metric</th><th>Detail</th>'
            '</tr></thead><tbody>'
        )
        for r in e["rows"]:
            wallet_cell = (
                f'<a href="{r["wallet_url"]}">{r["wallet"]}</a>'
                if r["wallet"] and r["wallet_url"]
                else (r["wallet"] or "—")
            )
            parts.append(
                f'<tr>'
                f'<td class="tag">{r["signal"]}</td>'
                f'<td class="mono">{wallet_cell}</td>'
                f'<td>{r["side"] or "—"}</td>'
                f'<td class="num">{r["notional_fmt"]}</td>'
                f'<td>{r["extra"]}</td>'
                f'</tr>'
            )
        parts.append("</tbody></table>")
    return "".join(parts)


def _row_notional(row: dict[str, Any]) -> str:
    """Pick the most meaningful money field for the row's signal."""
    for key in (
        "notional_fmt",
        "net_notional_fmt",
        "combined_notional_fmt",
        "volume_24h_fmt",
    ):
        val = row.get(key)
        if val:
            return str(val)
    return "—"


def _row_extra(row: dict[str, Any], signal_id: str) -> str:
    """A one-line 'why flagged' detail. Varies per signal."""
    if signal_id.startswith("01-A"):
        return f"first seen {row.get('first_seen_fmt', '—')} ago"
    if signal_id.startswith("01-B"):
        return f"{row.get('variant_display', '')} · {row.get('context_fmt', '')}"
    if signal_id.startswith("02-A"):
        return f"imbalance {row.get('imbalance_fmt', '')} · {row.get('trade_count', '')} trades"
    if signal_id.startswith("02-C"):
        return f"{row.get('wallet_count', '')} wallets · {row.get('span_s_fmt', '')}"
    if signal_id.startswith("03-A"):
        return f"{row.get('multiple_fmt', '')} vs baseline {row.get('baseline_fmt', '')}"
    return ""


def _top_markets_by_flagged(report: DailyReport) -> str:
    """Rank of markets by how much flagged notional they attracted.

    Different from the legacy "top by 24h volume" — here we rank on
    the signal hit rate + notional, which is what a reader cares
    about.
    """
    totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"title": "", "url": "", "notional": 0.0, "signals": set()}
    )
    for section in report.sections:
        for row in section.rows:
            mkey = row.get("market_url") or row.get("market_title", "")
            if not mkey:
                continue
            slot = totals[mkey]
            slot["title"] = row.get("market_title", "")
            slot["url"] = row.get("market_url", "")
            slot["signals"].add(section.title)
            notional = (
                row.get("notional")
                or row.get("net_notional")
                or row.get("combined_notional")
                or row.get("volume_24h")
                or 0
            )
            slot["notional"] += float(abs(notional))

    ordered = sorted(totals.values(), key=lambda r: r["notional"], reverse=True)[:20]
    if not ordered:
        return ""

    parts = [
        "<h2>Top markets by flagged notional</h2>",
        '<p class="sub">Markets ranked by combined notional across '
        'every signal that fired on them.</p>',
        '<table><thead><tr>'
        '<th style="width:60%">Market</th>'
        '<th>Signals</th>'
        '<th class="num">Flagged notional</th>'
        '</tr></thead><tbody>',
    ]
    for r in ordered:
        title_html = (
            f'<a href="{r["url"]}">{r["title"]}</a>' if r["url"] else r["title"]
        )
        sig_html = " ".join(f'<span class="signal-tag">{s}</span>' for s in sorted(r["signals"]))
        parts.append(
            f'<tr><td>{title_html}</td>'
            f'<td>{sig_html}</td>'
            f'<td class="num">${r["notional"]:,.0f}</td></tr>'
        )
    parts.append("</tbody></table>")
    return "".join(parts)


def _glossary(report: DailyReport) -> str:
    parts = ["<h2>Signal glossary</h2>"]
    parts.append(
        '<table><thead><tr><th>Signal</th><th>Reliability</th>'
        '<th>What it detects</th></tr></thead><tbody>'
    )
    for name, band, desc in report.glossary:
        parts.append(
            f'<tr><td><strong>{name}</strong></td>'
            f'<td class="tag">{band}</td>'
            f'<td>{desc}</td></tr>'
        )
    parts.append("</tbody></table>")
    return "".join(parts)


def _footer(report: DailyReport) -> str:
    return f"""
      <p class="muted" style="margin-top:18pt;border-top:1px solid #eee;padding-top:6pt">
        Generated from Polymarket data-api + gamma-api. Signal
        taxonomy: docs/SPEC-MARKET-SIGNALS.md. Every number in this
        document is derived from the attached raw-trade CSV and is
        independently verifiable. No outcome-scoring claims (see
        docs/SPEC-NEWSLETTERS-POLYMARKET.md § 7.1).
      </p>
    """
