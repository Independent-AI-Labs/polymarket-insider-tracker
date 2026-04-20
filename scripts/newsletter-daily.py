#!/usr/bin/env python3
"""Phase S1 daily newsletter — signal-registry-driven.

Pulls a 24 h size-meaningful trade window from data-api, enriches
with gamma-api metadata, hands both to `detector.composer.compose()`.
The composer walks the signal registry (`detector/signals/`) and
returns a `DailyReport` — a fully assembled data structure whose
every string the template renders verbatim.

ZERO hardcoded copy past the footer compliance fields. Adding a
signal never touches this file or the template — only
`detector/signals/` + `registry.py`.

Usage:
    uv run python scripts/newsletter-daily.py --no-send
    uv run python scripts/newsletter-daily.py --dry-run
    uv run python scripts/newsletter-daily.py
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from newsletter_common import deliver_via_himalaya  # noqa: E402

from polymarket_insider_tracker.detector.composer import compose  # noqa: E402
from polymarket_insider_tracker.detector.pdf_appendix import render_pdf  # noqa: E402
from polymarket_insider_tracker.detector.signals import REGISTRY, SignalContext  # noqa: E402
from polymarket_insider_tracker.detector.signals.base import (  # noqa: E402
    _badge_html,
    _market_state_html,
    _market_title_cell_html,
    _wallet_cell_html,
    _wallet_list_html,
    category_palette,
    signal_badges_html,
)
from polymarket_insider_tracker.detector.signals import icons as icons_mod  # noqa: E402

LOG = logging.getLogger("newsletter-daily")

TEMPLATES_DIR = SCRIPT_DIR / "templates"
DAILY_TEMPLATE = TEMPLATES_DIR / "polymarket-daily.html"
CONFIG_PATH = SCRIPT_DIR / "report-config.yaml"
REPORTS_DIR = PROJECT_ROOT / "reports"

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

DAILY_MIN_NOTIONAL = 10_000
MAX_HISTORICAL_OFFSET = 3000
PAGE_SIZE = 500
# How many top markets by 24h notional to fetch gamma metadata for.
MARKET_META_LIMIT = 50


# ── Data fetching ───────────────────────────────────────────────────


def fetch_live_trades(
    *,
    window_hours: float,
    min_notional: float,
) -> list[dict[str, Any]]:
    """Paginate data-api for a 24 h size-meaningful window."""
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    cutoff_ts = int(cutoff.timestamp())
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    with httpx.Client(timeout=30.0) as client:
        for offset in range(0, MAX_HISTORICAL_OFFSET + 1, PAGE_SIZE):
            resp = client.get(
                f"{DATA_API}/trades",
                params={
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "filterAmount": min_notional,
                },
            )
            if resp.status_code != 200:
                LOG.warning(
                    "data-api offset=%d returned HTTP %d; stopping",
                    offset,
                    resp.status_code,
                )
                break
            rows = resp.json()
            if not isinstance(rows, list) or not rows:
                break
            oldest_in_page: int | None = None
            for r in rows:
                tx = str(r.get("transactionHash", ""))
                if tx and tx in seen:
                    continue
                seen.add(tx)
                out.append(r)
                ts = int(r.get("timestamp", 0))
                if oldest_in_page is None or ts < oldest_in_page:
                    oldest_in_page = ts
            if oldest_in_page is not None and oldest_in_page <= cutoff_ts:
                break
    return [r for r in out if int(r.get("timestamp", 0)) >= cutoff_ts]


def fetch_market_meta(market_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Bulk-fetch gamma-api for every market appearing in trades."""
    if not market_ids:
        return {}
    out: dict[str, dict[str, Any]] = {}
    with httpx.Client(timeout=30.0) as client:
        for i in range(0, len(market_ids), 50):
            chunk = market_ids[i : i + 50]
            params = [("condition_ids", cid) for cid in chunk]
            params.append(("limit", str(len(chunk))))
            resp = client.get(f"{GAMMA_API}/markets", params=params)
            if resp.status_code != 200:
                LOG.warning(
                    "gamma condition_ids chunk failed: %d", resp.status_code
                )
                continue
            for m in resp.json():
                cid = str(m.get("conditionId", "")).lower()
                if cid:
                    out[cid] = m
    return out


# ── Context builder ─────────────────────────────────────────────────


def build_context(target_date: date) -> SignalContext:
    trades = fetch_live_trades(
        window_hours=24, min_notional=DAILY_MIN_NOTIONAL
    )
    LOG.info(
        "fetched %d trades >= $%d over 24h",
        len(trades),
        DAILY_MIN_NOTIONAL,
    )

    # Rank markets by 24h notional and fetch gamma metadata for
    # the top MARKET_META_LIMIT so volume-velocity + past-end-date
    # filtering in per-signal code has live gamma facts.
    from decimal import Decimal

    notional_by_market: dict[str, Decimal] = {}
    titles_by_market: dict[str, tuple[str, str]] = {}
    for t in trades:
        mid = str(t.get("conditionId", ""))
        if not mid:
            continue
        n = Decimal(str(t.get("size", 0))) * Decimal(str(t.get("price", 0)))
        notional_by_market[mid] = notional_by_market.get(mid, Decimal("0")) + n
        titles_by_market.setdefault(
            mid,
            (str(t.get("title", "")), str(t.get("eventSlug", ""))),
        )

    top_market_ids = sorted(
        notional_by_market.keys(),
        key=lambda mid: notional_by_market[mid],
        reverse=True,
    )[:MARKET_META_LIMIT]

    market_meta = fetch_market_meta(top_market_ids)

    # Post-filter: exclude markets past their endDate. Signals that
    # need the gate also check in their own code, but pruning here
    # keeps unnecessary markets out of the meta dict.
    now = datetime.now(UTC)
    kept: dict[str, dict[str, Any]] = {}
    for cid, m in market_meta.items():
        if bool(m.get("closed")) is True:
            continue
        end_raw = str(m.get("endDate", "") or m.get("endDateIso", ""))
        if end_raw:
            try:
                end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
            except ValueError:
                kept[cid] = m
                continue
            if end_dt < now:
                continue
        kept[cid] = m

    live_market_ids = set(kept.keys())
    trades_kept = [
        t for t in trades
        if str(t.get("conditionId", "")).lower() in live_market_ids
    ]

    window_end = datetime.now(UTC)
    window_start = window_end - timedelta(hours=24)
    if trades_kept:
        ts_values = [int(t.get("timestamp", 0)) for t in trades_kept]
        window_start = datetime.fromtimestamp(min(ts_values), UTC)
        window_end = datetime.fromtimestamp(max(ts_values), UTC)

    return SignalContext(
        trades=trades_kept,
        market_meta=kept,
        window_start=window_start,
        window_end=window_end,
        edition_date=target_date.isoformat(),
    )


# ── CSV attachment ──────────────────────────────────────────────────


def write_alerts_csv(raw_alerts: list[dict], date_str: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"alerts-{date_str}.csv"
    if not raw_alerts:
        path.write_text("# no trades in window\n", encoding="utf-8")
        return path
    fieldnames = list(raw_alerts[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(raw_alerts)
    return path


# ── Tera-payload serializer ─────────────────────────────────────────


def _glossary_row_for_email(name: str, band: str, desc: str) -> dict[str, Any]:
    """Resolve a glossary row's category via the registry so the
    email template can render the same category badge the PDF uses.
    """
    category = ""
    for sig in REGISTRY:
        if sig.name == name:
            category = sig.category
            break
    category_badge = ""
    if category:
        label = category_palette(category)["label"]
        category_badge = _badge_html(label, category)
    return {
        "name": name,
        "band": band,
        "desc": desc,
        "category_badge_html": category_badge,
    }


def _safe_float(*candidates) -> float | None:
    """Return the first candidate coercible to float, else None."""
    for c in candidates:
        if c is None or c == "":
            continue
        try:
            return float(c)
        except (TypeError, ValueError):
            continue
    return None


def _section_to_payload(
    section,
    market_meta: dict[str, dict[str, Any]] | None = None,
    velocity_flagged: set[str] | None = None,
) -> dict[str, Any]:
    """Reshape the section into a template-friendly payload.

    Any pre-rendered HTML fields on `row` (wallet_cell_html,
    top_wallets_fmt) were rendered at compose time in whatever icon
    mode was active then. Re-render here so the current icon mode
    (CID for email) governs every badge + blockie occurrence.
    """
    cols = [asdict(c) for c in section.columns]
    market_meta = market_meta or {}
    velocity_flagged = velocity_flagged or set()
    rendered_rows = []
    for row in section.rows:
        # Rehydrate mode-sensitive HTML in-place.
        if row.get("wallet_address"):
            row["wallet_cell_html"] = _wallet_cell_html(row["wallet_address"])
        if row.get("top_wallets"):
            row["top_wallets_fmt"] = _wallet_list_html([
                (str(tw.get("address", "")), float(tw.get("amount", 0)))
                for tw in row["top_wallets"]
            ])

        # Round 1C: rebuild the market-title cell as HTML — link +
        # 16×16 donut (lastTradePrice) + volume pill (volume24hr).
        mid = str(row.get("market_id", "")).lower()
        meta = market_meta.get(mid) or {}
        last_trade = _safe_float(
            row.get("last_trade_price"),
            meta.get("lastTradePrice"),
        )
        volume_24h = _safe_float(
            row.get("volume_24h"),
            meta.get("volume24hr"),
        )
        flagged = True  # surfaced-by-signal ⇒ bronze
        # If the volume-velocity signal explicitly deemed this market
        # below the 3× threshold (can't happen here — it only emits
        # flagged hits — but the dial exists for future signals), we
        # grey it out. Default stays bronze.
        if row.get("multiple") is not None:
            try:
                flagged = float(row["multiple"]) >= 3.0
            except (TypeError, ValueError):
                flagged = True
        elif mid and velocity_flagged:
            flagged = True  # (explicit)

        cells = []
        for col in section.columns:
            value = row.get(col.field, "")
            link_url = row.get(col.link_field, "") if col.link_field else ""
            format_hint = col.format_hint
            # Replace the plain-text market-title cell with an HTML
            # cell that carries the donut + pill.
            if col.field == "market_title" and value:
                value = _market_title_cell_html(
                    str(value),
                    row.get("market_url", "") or link_url,
                    last_trade_price=last_trade,
                    volume_24h=volume_24h,
                    flagged=flagged,
                )
                format_hint = "html"
                link_url = ""  # link is inside the HTML already
            cells.append(
                {
                    "value": value,
                    "align": col.align,
                    "format_hint": format_hint,
                    "link_url": link_url,
                }
            )
        rendered_rows.append({"cells": cells})
    return {
        "signal_id": section.signal_id,
        "category": section.category,
        "title": section.title,
        "subtitle": section.subtitle,
        "reliability_band": section.reliability_band,
        "column_headers": [
            {"header": c["header"], "align": c["align"]} for c in cols
        ],
        "rows": rendered_rows,
    }


def report_to_tera_payload(report, cfg: dict[str, Any]) -> dict[str, Any]:
    window_fmt = (
        f"{report.window_start:%Y-%m-%d %H:%M} → "
        f"{report.window_end:%Y-%m-%d %H:%M} UTC "
        f"({(report.window_end - report.window_start).total_seconds() / 3600:.1f} h)"
    )
    promoted = getattr(report, "promoted_markets", None) or []
    watches = getattr(report, "wallets_to_watch", None) or []
    market_meta: dict[str, dict[str, Any]] = getattr(report, "market_meta", {}) or {}
    velocity_flagged: set[str] = getattr(report, "velocity_flagged_markets", set()) or set()
    cross_market_watches = [w for w in watches if w.market_count >= 2]

    def _state_for(mid: str) -> tuple[float | None, float | None]:
        meta = market_meta.get((mid or "").lower(), {}) or {}
        return (
            _safe_float(meta.get("lastTradePrice")),
            _safe_float(meta.get("volume24hr")),
        )

    return {
        "date": report.date,
        "window_fmt": window_fmt,
        "edition_id": report.edition_id,
        "summary": [list(pair) for pair in report.summary],
        "headline": report.headline,
        "source_label": report.source_label,
        "sections": [
            _section_to_payload(s, market_meta, velocity_flagged)
            for s in report.sections
        ],
        "glossary": [
            _glossary_row_for_email(name, band, desc)
            for (name, band, desc) in report.glossary
        ],
        "footer_legal_name": report.footer_legal_name,
        "footer_postal_address": report.footer_postal_address,
        "promoted_markets": [
            _promoted_market_payload(p, market_meta)
            for p in promoted
        ],
        "cross_market_wallets": [
            {
                "wallet_cell_html": _wallet_cell_html(w.address),
                "address_display": w.address_display,
                "profile_url": w.profile_url,
                "market_count": w.market_count,
                "total_notional_fmt": f"${w.total_notional:,.0f}",
                "is_fresh": w.is_fresh,
                "first_seen_fmt": w.first_seen_fmt,
                "category_badges_html": w.category_badges_html,
                "markets": [
                    _cross_wallet_market_payload(m, market_meta)
                    for m in w.markets
                ],
            }
            for w in cross_market_watches[:5]
        ],
    }


def _promoted_market_payload(
    p: Any, market_meta: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    meta = market_meta.get((p.market_id or "").lower(), {}) or {}
    last_trade = _safe_float(meta.get("lastTradePrice"))
    volume_24h = _safe_float(meta.get("volume24hr"))
    return {
        "market_title": p.market_title,
        "market_url": p.market_url,
        "market_title_cell_html": _market_title_cell_html(
            p.market_title,
            p.market_url,
            last_trade_price=last_trade,
            volume_24h=volume_24h,
            flagged=True,
        ),
        "categories": p.categories,
        "category_badges_html": p.category_badges_html,
        "signal_names": p.signal_names,
        "signal_badges_html": p.signal_badges_html,
        "total_notional": p.total_notional,
        "total_notional_fmt": f"${p.total_notional:,.0f}",
        "category_count": len(p.categories),
    }


def _cross_wallet_market_payload(
    m: dict[str, Any], market_meta: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    mid = str(m.get("market_id", "")).lower()
    meta = market_meta.get(mid, {}) or {}
    last_trade = _safe_float(meta.get("lastTradePrice"))
    volume_24h = _safe_float(meta.get("volume24hr"))
    return {
        "title": m["title"],
        "url": m["url"],
        "signals_fmt": m.get("signals_fmt", ""),
        # Re-render in the current icon mode (so CID refs register
        # when we're building the email payload).
        "signals_badges_html": signal_badges_html(
            sorted(m.get("signals_with_category", {}).items())
        ),
        "market_title_cell_html": _market_title_cell_html(
            m.get("title", ""),
            m.get("url", ""),
            last_trade_price=last_trade,
            volume_24h=volume_24h,
            flagged=True,
        ),
        "notional_fmt": f"${m['notional']:,.0f}",
    }


# ── Delivery ────────────────────────────────────────────────────────


def build_rows(
    payload: dict[str, Any],
    cfg: dict[str, Any],
    targets_filter: str | None,
    inline_images: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    targets = cfg["delivery"]["targets"]
    if targets_filter:
        wanted = {t.strip() for t in targets_filter.split(",")}
        targets = [t for t in targets if t["name"] in wanted]
    else:
        targets = [t for t in targets if t.get("enabled", True)]

    subject = f"[AMI] Polymarket Watchlist — {payload['date']}"
    rows: list[dict[str, Any]] = []
    for t in targets:
        rows.append(
            {
                "email": t["email"],
                "name": t.get("name", t["email"]),
                "subject": subject,
                "reason": "you're on the canary list for Polymarket newsletter iteration",
                "unsubscribe_url": "https://example.invalid/unsubscribe?token=canary-placeholder",
                "report": payload,
                "style": cfg["email"]["style"],
                # One entry per inline image. Each will render as a
                # `<#part filename=… content-id=…>` inside the
                # multipart/related wrapper in the template. The
                # body's <img src="cid:…"> tags point at these parts.
                "inline_images": inline_images or [],
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase S1 daily newsletter")
    parser.add_argument(
        "--date",
        default=datetime.now(UTC).date().isoformat(),
        help="Edition date YYYY-MM-DD (default: today UTC)",
    )
    parser.add_argument("--no-send", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--targets", default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    with CONFIG_PATH.open() as fh:
        cfg = yaml.safe_load(fh)

    target_date = date.fromisoformat(args.date)

    # Compose once. We'll re-render the visuals twice: once in CID
    # mode (for the email MIME) and once in data-URI mode (for the
    # PDF which has no MIME structure).
    context = build_context(target_date)
    report = compose(
        context,
        source_label="data-api live feed + gamma-api metadata",
    )
    report.footer_legal_name = "Independent AI Labs"
    report.footer_postal_address = "ami-reports@independentailabs.com"

    LOG.info(
        "composed report: %d sections, %d total hits, headline=%s",
        len(report.sections),
        sum(len(s.rows) for s in report.sections),
        report.headline[:100],
    )

    # Outer attachments — CSV + PDF — these go at the multipart/mixed
    # level, alongside the multipart/related containing the HTML body
    # and its inline CID images.
    attachments: list[Path] = []
    csv_path = write_alerts_csv(report.raw_alerts, report.date, REPORTS_DIR)
    LOG.info("wrote %s (%d rows)", csv_path, len(report.raw_alerts))
    attachments.append(csv_path)

    # PDF render uses data-URIs so no MIME plumbing is needed there.
    icons_mod.set_render_mode("data_uri")
    icons_mod.reset_render_pass()
    pdf_path = REPORTS_DIR / f"polymarket-appendix-{report.date}.pdf"
    try:
        render_pdf(report, pdf_path)
        attachments.append(pdf_path)
        LOG.info("wrote appendix PDF %s", pdf_path)
    except Exception:
        LOG.exception("appendix PDF render failed; shipping without PDF")

    if args.no_send:
        LOG.info("--no-send: rendered context + CSV only")
        LOG.info("headline: %s", report.headline)
        for s in report.sections:
            LOG.info("  section %s: %d rows", s.signal_id, len(s.rows))
        return 0

    # Email render uses proper CID attachments: multipart/related
    # carries one `image/png` MIME part per distinct icon / blockie,
    # referenced via `<img src="cid:…">` in the HTML body. Survives
    # every mainstream client including Gmail web. Enabled by our
    # local mml-lib patch that adds `content-id=` attribute support
    # — upstream PR tracked in AMI-STREAMS/pimalaya-core fork.
    icons_mod.set_render_mode("cid")
    icons_mod.reset_render_pass()
    payload = report_to_tera_payload(report, cfg)
    inline_images = [
        {"cid": cid, "path": path}
        for cid, path in icons_mod.used_cid_parts()
    ]
    LOG.info("email: %d inline CID images", len(inline_images))
    rows = build_rows(payload, cfg, args.targets, inline_images)
    rc = deliver_via_himalaya(
        rows,
        template_path=DAILY_TEMPLATE,
        subject_template="{{ subject }}",
        account=cfg["delivery"]["account"],
        rate=cfg["delivery"].get("rate", "2/min"),
        attachments=attachments,
        dry_run=args.dry_run,
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
