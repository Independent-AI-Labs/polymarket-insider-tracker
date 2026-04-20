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
    parts.append(_wallets_to_watch(report))
    parts.append(_cross_signal_tier(report))
    parts.append(_by_market_log(report))
    parts.append(_glossary(report))
    parts.append(_footer(report))
    parts.append("</body></html>")
    return "\n".join(parts)


def _wallets_to_watch(report: DailyReport) -> str:
    """Wallet-centric tier — the analyst's view.

    Priority-ranked: wallets on multiple markets are at the top,
    fresh wallets highlighted. Single-market wallets still appear
    but below the cross-market ones. Retires the redundant
    "Top markets by flagged notional" section that just echoed
    the flagged-activity log.
    """
    watches = getattr(report, "wallets_to_watch", None) or []
    if not watches:
        return ""

    cross_market = [w for w in watches if w.market_count >= 2]
    single_market = [w for w in watches if w.market_count == 1]

    parts = ['<h2>Wallets to watch</h2>']
    if cross_market:
        parts.append(
            '<h3 style="margin:8pt 0 4pt;color:#8a4b00">'
            f'Cross-market wallets ({len(cross_market)}) '
            '— appear on ≥ 2 flagged markets</h3>'
        )
        parts.append(
            '<p class="muted">Strongest tell: the same proxy wallet '
            'shows up on multiple flagged markets the same day. Either '
            'an organised operator, a pro trader with a macro view, or '
            'an insider with scope across correlated outcomes.</p>'
        )
        parts.append(_watches_table(cross_market))

    # Cap single-market list — the cross-market tier is what M.
    # actually cares about.
    top_single = single_market[:15]
    if top_single:
        parts.append(
            '<h3 style="margin:12pt 0 4pt;color:#555">'
            f'Single-market wallets (top {len(top_single)} by notional)</h3>'
        )
        parts.append(_watches_table(top_single))
    return "".join(parts)


def _watches_table(watches) -> str:
    parts = [
        '<table><thead><tr>'
        '<th>Wallet</th>'
        '<th>First seen</th>'
        '<th class="num">Total flagged</th>'
        '<th class="num">Markets</th>'
        '<th>Markets / signals</th>'
        '</tr></thead><tbody>'
    ]
    for w in watches:
        markets_cell = "<br>".join(
            (
                f'<a href="{m["url"]}">{m["title"]}</a> '
                f'<span class="muted">'
                f'({", ".join(sorted(m["signals"]))})</span>'
                f'{"  ★" if m.get("promoted") else ""}'
            )
            for m in w.markets[:5]
        )
        fresh_tag = (
            ' <span class="signal-tag" style="background:#fff3cd;color:#8a4b00">fresh</span>'
            if w.is_fresh else ""
        )
        first_seen = w.first_seen_fmt or "—"
        parts.append(
            f'<tr>'
            f'<td class="mono"><a href="{w.profile_url}">{w.address_display}</a>{fresh_tag}</td>'
            f'<td>{first_seen}</td>'
            f'<td class="num">${w.total_notional:,.0f}</td>'
            f'<td class="num">{w.market_count}</td>'
            f'<td>{markets_cell}</td>'
            f'</tr>'
        )
    parts.append("</tbody></table>")
    return "".join(parts)


def _cross_signal_tier(report: DailyReport) -> str:
    """Top tier of the PDF — markets where ≥ 2 signal categories fired.

    This is what M. actually reads. Single-signal hits move to the
    log below; the cross-signal tier is the "pay attention" list.
    """
    promoted = getattr(report, "promoted_markets", None) or []
    if not promoted:
        return (
            '<h2>Cross-signal markets</h2>'
            '<p class="muted">No market fired ≥ 2 distinct signal '
            'categories this window. Single-signal hits are in the '
            'log below — treat them as informational, not as a buy '
            'recommendation.</p>'
        )
    parts = [
        '<h2>Cross-signal markets</h2>',
        '<p class="sub">Markets where ≥ 2 distinct signal categories '
        'fired. These are the "multiple lenses agree" cases — '
        'everything below this tier is a single-signal hit.</p>',
    ]
    for p in promoted:
        title_html = (
            f'<a href="{p.market_url}">{p.market_title}</a>'
            if p.market_url else p.market_title
        )
        tags = " ".join(
            f'<span class="signal-tag">{s}</span>' for s in p.signal_names
        )
        parts.append(
            f'<h3>{title_html} {tags}</h3>'
            f'<p class="muted">Categories firing: '
            f'{", ".join(p.categories)} · Combined notional: '
            f'${p.total_notional:,.0f}</p>'
        )
        parts.append(
            '<table><thead><tr>'
            '<th>Signal</th><th>Wallet / contributors</th>'
            '<th>Side</th><th class="num">Notional</th><th>Detail</th>'
            '</tr></thead><tbody>'
        )
        for h in p.hit_details:
            row = h["row"]
            wallet = row.get("wallet_display", "")
            wallet_url = row.get("wallet_url", "")
            if wallet and wallet_url:
                wallet_cell = f'<a href="{wallet_url}" class="mono">{wallet}</a>'
            else:
                # Market-level signal — fall back to top_wallets_fmt.
                wallet_cell = row.get("top_wallets_fmt", "—") or "—"
            extra = _row_extra(row, h["signal_id"])
            parts.append(
                f'<tr>'
                f'<td class="tag">{h["signal_name"]}</td>'
                f'<td>{wallet_cell}</td>'
                f'<td>{row.get("side", "") or "—"}</td>'
                f'<td class="num">{_row_notional(row)}</td>'
                f'<td>{extra}</td>'
                f'</tr>'
            )
        parts.append("</tbody></table>")
    return "".join(parts)


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
    # Canonicalise on `market_id` (lowercase conditionId). Prior
    # versions keyed on market_url-or-title and double-counted the
    # same market under two slightly-different row shapes.
    by_market: dict[str, dict[str, Any]] = {}
    for section in report.sections:
        signal_id = section.signal_id
        signal_name = section.title
        for row in section.rows:
            mid = (row.get("market_id") or "").lower()
            if not mid:
                continue
            entry = by_market.setdefault(
                mid,
                {
                    "market_id": mid,
                    "title": row.get("market_title", ""),
                    "url": row.get("market_url", ""),
                    "signals": set(),
                    "rows": [],
                    "total_notional": 0.0,
                },
            )
            # Keep the first non-empty title/url we see (they should
            # all agree, but be defensive).
            if not entry["title"] and row.get("market_title"):
                entry["title"] = row.get("market_title")
            if not entry["url"] and row.get("market_url"):
                entry["url"] = row.get("market_url")
            entry["signals"].add(signal_name)
            notional = (
                row.get("notional")
                or row.get("net_notional")
                or row.get("combined_notional")
                or row.get("volume_24h")
                or 0
            )
            try:
                entry["total_notional"] += float(abs(notional))
            except (TypeError, ValueError):
                pass
            # Pick wallet display — use explicit wallet_display on
            # wallet-level rows, fall back to top_wallets_fmt on
            # market-level rows.
            wallet = row.get("wallet_display", "")
            wallet_url = row.get("wallet_url", "")
            if not wallet:
                wallet = row.get("top_wallets_fmt", "") or "—"
                wallet_url = ""
            entry["rows"].append(
                {
                    "signal": signal_name,
                    "signal_id": signal_id,
                    "wallet": wallet,
                    "wallet_url": wallet_url,
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
        seen = row.get("first_seen_fmt", "—")
        avg = row.get("avg_price_fmt", "")
        payoff = row.get("max_payoff_fmt", "")
        bits = [f"first seen {seen} ago"]
        if avg:
            bits.append(f"avg {avg}")
        if payoff:
            bits.append(f"payoff ≤ {payoff}")
        return " · ".join(bits)
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
            mid = (row.get("market_id") or "").lower()
            if not mid:
                continue
            slot = totals[mid]
            if not slot["title"]:
                slot["title"] = row.get("market_title", "")
            if not slot["url"]:
                slot["url"] = row.get("market_url", "")
            slot["signals"].add(section.title)
            notional = (
                row.get("notional")
                or row.get("net_notional")
                or row.get("combined_notional")
                or row.get("volume_24h")
                or 0
            )
            try:
                slot["notional"] += float(abs(notional))
            except (TypeError, ValueError):
                pass

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
