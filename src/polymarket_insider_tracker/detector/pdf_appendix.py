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

from .signals import DailyReport, REGISTRY
from .signals.base import (
    CATEGORY_PALETTE,
    _short_wallet,
    _wallet_cell_html,
    category_badges_html,
    category_palette,
    signal_badges_html,
)

LOG = logging.getLogger(__name__)

CSS = """
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    font-size: 9.5pt; color: #111; line-height: 1.45;
    font-variant-numeric: tabular-nums;
  }
  h1 { font-size: 18pt; margin: 0 0 4pt; letter-spacing: -0.01em; }
  h2 { font-size: 11pt; margin: 16pt 0 4pt; color: #111;
       text-transform: uppercase; letter-spacing: 0.08em;
       font-weight: 700; padding-left: 8pt;
       border-left: 3px solid #111; }
  h3 { font-size: 10pt; margin: 10pt 0 3pt; color: #222;
       font-weight: 600; letter-spacing: -0.005em; }
  p  { margin: 2pt 0; }
  .sub { color: #666; font-size: 9pt; margin-bottom: 6pt; }
  .muted { color: #888; font-size: 8.5pt; }
  a { color: #1a5fb4; text-decoration: none; }
  table { width: 100%; border-collapse: collapse; margin: 4pt 0 8pt;
          font-size: 8.5pt; }
  th, td { padding: 4pt 6pt; border-bottom: 1px solid #eee;
           text-align: left; vertical-align: top; }
  th { background: #fafafa; font-weight: 600; color: #444;
       letter-spacing: 0.02em; font-size: 7.5pt;
       text-transform: uppercase; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.mono { font-family: ui-monospace, 'SF Mono', Menlo, monospace;
            font-size: 8pt; }
  td.tag { font-size: 8pt; color: #555; }
  thead { display: table-header-group; }
  tr, th, td { page-break-inside: avoid; }

  /* Cover card: warm, restrained. A 2pt bronze accent line below
     sets the document apart without shouting. */
  .cover { background: #1a1a2e; color: #fff; padding: 18pt 22pt;
           margin: 0 0 0; border-radius: 3pt 3pt 0 0; }
  .cover .eyebrow { color: #c9c9d6; text-transform: uppercase;
                    letter-spacing: 0.16em; font-size: 7.5pt;
                    font-weight: 600; }
  .cover h1 { margin: 6pt 0 0; color: #fff; font-weight: 600; }
  .cover p  { color: #a8a8b8; margin: 4pt 0 0; font-size: 8.5pt;
              letter-spacing: 0.02em; }
  .cover-rule { height: 2pt; background: #b45309; width: 48pt;
                margin: 0 0 14pt; }
  .kpi { display: table; margin: 12pt 0 0; font-size: 9pt; }
  .kpi .row { display: table-row; }
  .kpi .cell { display: table-cell; padding-right: 20pt;
               color: #a8a8b8; font-size: 8pt;
               letter-spacing: 0.02em; }
  .kpi .val  { display: table-cell; padding-right: 20pt; color: #fff;
               font-weight: 700; font-size: 10.5pt; }

  /* Category badges — tone-on-tone, one color per category,
     shared with the email template via base.CATEGORY_PALETTE. */
  .badge { display: inline-block; padding: 1pt 6pt;
           border-radius: 3pt; font-size: 7.5pt; font-weight: 600;
           letter-spacing: 0.02em; margin-right: 3pt;
           border: 1px solid; white-space: nowrap; }
  .badge.informed_flow   { color: #1e3a8a; background: #eff6ff; border-color: #bfdbfe; }
  .badge.microstructure  { color: #475569; background: #f8fafc; border-color: #cbd5e1; }
  .badge.volume_liquidity{ color: #92400e; background: #fffbeb; border-color: #fde68a; }
  .badge.price_dynamics  { color: #7e22ce; background: #faf5ff; border-color: #e9d5ff; }
  .badge.event_catalyst  { color: #065f46; background: #f0fdf4; border-color: #bbf7d0; }
  .badge.cross_market    { color: #0e7490; background: #ecfeff; border-color: #a5f3fc; }

  /* Fresh wallet inline flag. */
  .fresh-flag { display: inline-block; padding: 1pt 5pt;
                border-radius: 3pt; font-size: 7pt; font-weight: 600;
                color: #92400e; background: #fffbeb;
                border: 1px solid #fde68a; margin-left: 4pt; }

  /* Accent cards — restrained paper-tones, not loud banners. */
  .card-cross-wallet  { border-left: 3px solid #b45309;
                        background: #fefdf8;
                        padding: 14pt 16pt; margin: 0 0 14pt; }
  .card-cross-wallet  h2 { border-left-width: 0; padding-left: 0;
                           color: #92400e; margin-top: 0; }
  .card-cross-signal  { border-left: 3px solid #1e3a8a;
                        background: #fafbff;
                        padding: 14pt 16pt; margin: 0 0 14pt; }
  .card-cross-signal  h2 { border-left-width: 0; padding-left: 0;
                           color: #1e3a8a; margin-top: 0; }
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

    parts = []
    if cross_market:
        parts.append('<div class="card-cross-wallet">')
        parts.append('<h2>Wallets to watch · cross-market</h2>')
        parts.append(
            '<p class="sub">Same wallet appears on multiple flagged '
            'markets — strongest informed-flow tell we can report '
            'without outcome data.</p>'
        )
        parts.append(_watches_table(cross_market, include_markets=True))
        parts.append('</div>')

    top_single = single_market[:15]
    if top_single:
        parts.append('<h2>Wallets to watch · single-market (top by notional)</h2>')
        parts.append(_watches_table(top_single, include_markets=True))
    return "".join(parts)


def _watches_table(watches, *, include_markets: bool = True) -> str:
    parts = [
        '<table><thead><tr>'
        '<th>Wallet</th>'
        '<th>First seen</th>'
        '<th class="num">Total flagged</th>'
        '<th class="num">Markets</th>'
    ]
    if include_markets:
        parts.append('<th>Appears on</th>')
    parts.append('</tr></thead><tbody>')
    for w in watches:
        markets_cell = ""
        if include_markets:
            rows = []
            for m in w.markets[:5]:
                star = (
                    ' <span style="color:#b45309;font-weight:700">★</span>'
                    if m.get("promoted") else ""
                )
                rows.append(
                    f'<div style="margin-bottom:3pt">'
                    f'<a href="{m["url"]}" style="color:#111;font-weight:500">{m["title"]}</a>{star}<br>'
                    f'<span>{m.get("signals_badges_html", "")}</span> '
                    f'<span class="muted">${m["notional"]:,.0f}</span>'
                    f'</div>'
                )
            markets_cell = "".join(rows)
        fresh_tag = ' <span class="fresh-flag">fresh</span>' if w.is_fresh else ""
        first_seen = w.first_seen_fmt or "—"
        wallet_cell = _wallet_cell_html(w.address)
        parts.append(
            f'<tr>'
            f'<td>{wallet_cell}{fresh_tag}</td>'
            f'<td>{first_seen}</td>'
            f'<td class="num">${w.total_notional:,.0f}</td>'
            f'<td class="num">{w.market_count}</td>'
        )
        if include_markets:
            parts.append(f'<td>{markets_cell}</td>')
        parts.append('</tr>')
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
            'log below — informational, not actionable.</p>'
        )
    parts = [
        '<div class="card-cross-signal">',
        '<h2>Cross-signal markets</h2>',
        '<p class="sub">Markets where ≥ 2 distinct signal categories '
        'fired — multiple lenses agreeing on the same market.</p>',
    ]
    for p in promoted:
        title_html = (
            f'<a href="{p.market_url}">{p.market_title}</a>'
            if p.market_url else p.market_title
        )
        cat_badges = p.category_badges_html
        sig_badges = p.signal_badges_html
        parts.append(
            f'<h3>{title_html}</h3>'
            f'<p class="muted" style="margin:2pt 0 6pt">'
            f'{cat_badges} &nbsp; <span style="color:#888">Notional: '
            f'${p.total_notional:,.0f}</span></p>'
            f'<p class="muted" style="margin:0 0 6pt">{sig_badges}</p>'
        )
        parts.append(
            '<table><thead><tr>'
            '<th>Signal</th><th>Wallet / contributors</th>'
            '<th>Side</th><th class="num">Notional</th><th>Detail</th>'
            '</tr></thead><tbody>'
        )
        for h in p.hit_details:
            row = h["row"]
            wallet_addr = row.get("wallet_address", "")
            if wallet_addr:
                wallet_cell = _wallet_cell_html(wallet_addr)
            else:
                # Market-level signal — contributor list already
                # embeds identicons via _wallet_list_html.
                wallet_cell = row.get("top_wallets_fmt", "—") or "—"
            extra = _row_extra(row, h["signal_id"])
            sig_badge = _signal_badge_with_icon(h["signal_name"], h["category"])
            parts.append(
                f'<tr>'
                f'<td>{sig_badge}</td>'
                f'<td>{wallet_cell}</td>'
                f'<td>{row.get("side", "") or "—"}</td>'
                f'<td class="num">{_row_notional(row)}</td>'
                f'<td>{extra}</td>'
                f'</tr>'
            )
        parts.append("</tbody></table>")
    parts.append("</div>")
    return "".join(parts)


def _signal_badge_with_icon(signal_name: str, category: str) -> str:
    """Small inline badge for signal names — same renderer as the
    email so PDF and mail stay in lock-step. Delegates to the
    shared `_badge_html` which emits the PNG-icon data-URI.
    """
    from .signals.base import _badge_html
    return _badge_html(signal_name, category, size="sm")


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
        <p>{window_fmt}</p>
        <p>{report.edition_id} · {report.source_label}</p>
        <div class="kpi">{summary_html}</div>
      </div>
      <div class="cover-rule"></div>
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
            # Pick wallet cell — explicit address → blockie+link,
            # otherwise fall back to the pre-rendered contributor
            # list (which itself uses _wallet_list_html for blockies).
            wallet_addr = row.get("wallet_address", "")
            if wallet_addr:
                wallet_cell = _wallet_cell_html(wallet_addr)
            else:
                wallet_cell = row.get("top_wallets_fmt", "") or "—"
            entry["rows"].append(
                {
                    "signal": signal_name,
                    "signal_id": signal_id,
                    "wallet_cell": wallet_cell,
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
        # Signal set → category badges in consistent order.
        cats_for_market: dict[str, str] = {}  # signal → category
        for r in e["rows"]:
            sid = r.get("signal_id", "")
            for sig in REGISTRY:
                if sig.id == sid:
                    cats_for_market[r["signal"]] = sig.category
                    break
        badges_html = signal_badges_html(sorted(cats_for_market.items()))
        title_html = (
            f'<a href="{e["url"]}">{e["title"]}</a>' if e["url"] else e["title"]
        )
        parts.append(
            f'<h3>{title_html}</h3>'
            f'<p class="muted" style="margin:2pt 0 6pt">{badges_html}</p>'
        )
        parts.append(
            '<table><thead><tr>'
            '<th>Signal</th><th>Wallet</th><th>Side</th>'
            '<th class="num">Notional / metric</th><th>Detail</th>'
            '</tr></thead><tbody>'
        )
        for r in e["rows"]:
            cat = cats_for_market.get(r["signal"], "")
            sig_badge = (
                _signal_badge_with_icon(r["signal"], cat)
                if cat else r["signal"]
            )
            parts.append(
                f'<tr>'
                f'<td>{sig_badge}</td>'
                f'<td>{r["wallet_cell"]}</td>'
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
        '<table><thead><tr><th>Signal</th><th>Category</th>'
        '<th>Reliability</th><th>What it detects</th></tr></thead><tbody>'
    )
    for name, band, desc in report.glossary:
        category = ""
        for sig in REGISTRY:
            if sig.name == name:
                category = sig.category
                break
        cat_badge = (
            f'<span class="badge {category}">'
            f'{category_palette(category)["label"]}</span>'
            if category else ""
        )
        parts.append(
            f'<tr><td><strong>{name}</strong></td>'
            f'<td>{cat_badge}</td>'
            f'<td class="muted">{band}</td>'
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
