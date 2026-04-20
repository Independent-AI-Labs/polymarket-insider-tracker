"""Base classes + dataclasses for the pluggable signal framework.

Every signal in `docs/signals/*.md` has a corresponding Python module
here. A signal is a small class with:

- Metadata (id, category, display name, reliability band)
- A `compute(context)` method that returns a list of `SignalHit`s
- A `section_spec(hits)` method that describes how those hits
  render into the newsletter (title, subtitle, columns, rows)

The composer (`detector/composer.py`) iterates over every registered
signal, calls `compute` + `section_spec`, and assembles a
`DailyReport`. The Tera template is a generic iterator over that
report — it never hardcodes a section title, column header, or
headline sentence. Adding / retiring a signal never touches the
template or the composer.

This is what "automate title generation and everything else" means
in practice.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal


# ── Row / column primitives ──────────────────────────────────────────


ColumnAlignment = Literal["left", "right", "center"]


@dataclass(frozen=True)
class ColumnSpec:
    """How one column renders in a signal's section table.

    `field` names a key in each hit's `row` dict. `format_hint` is a
    loose enum the template honours (`money`, `int`, `percent`,
    `bps`, `wallet`, `link`, `text`, `datetime`, `duration`) so the
    same underlying number can render consistently across signals.
    """

    field: str
    header: str
    align: ColumnAlignment = "left"
    format_hint: str = "text"
    width_hint: str = ""  # e.g. "40%", "120px", or "" for auto
    link_field: str = ""  # when set, render the cell as a link to
                          # the URL stored at row[link_field]


@dataclass(frozen=True)
class SignalHit:
    """A single detection for a (wallet, market, window) triple."""

    signal_id: str
    wallet_address: str        # "" for market-level signals
    market_id: str
    market_title: str
    event_slug: str
    score: float               # ∈ [0, 1]
    row: dict[str, Any]        # column-field → value

    # Headline contribution — one plain sentence this hit suggests
    # be rendered as part of the top-of-email headline. The composer
    # picks at most 2 across all signals. Empty string ⇒ this hit
    # doesn't claim headline space.
    headline_fragment: str = ""


@dataclass(frozen=True)
class SectionSpec:
    """What a signal's section looks like in the rendered newsletter.

    Everything the template needs to render the section sits here.
    The template NEVER has `{% if signal.id == "..." %}` — it just
    iterates `report.sections` and calls the column format handler.
    """

    signal_id: str
    category: str                       # e.g. "informed_flow"
    title: str                          # "Asymmetric flow", etc.
    subtitle: str                       # one-sentence explanation
    columns: list[ColumnSpec]
    rows: list[dict[str, Any]]
    reliability_band: Literal["high", "medium", "low"] = "medium"

    # When True, hide the section if there are no rows. When False,
    # render "No hits this window." so the reader knows the signal
    # ran but found nothing. Most signals use `hide_when_empty=True`
    # to keep the email tight.
    hide_when_empty: bool = True


# ── Signal base class ────────────────────────────────────────────────


@dataclass
class SignalContext:
    """Everything a signal needs to compute hits.

    The composer builds this once per run and passes it to every
    signal; signals don't talk to data-api / gamma-api directly so
    they're trivially testable (feed synthetic context, assert on
    hits).
    """

    # Newest-first list of trade dicts from data-api for the window.
    trades: list[dict[str, Any]] = field(default_factory=list)
    # Lowercased conditionId → gamma-api market metadata dict.
    market_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Window boundaries.
    window_start: datetime | None = None
    window_end: datetime | None = None
    # Edition date (YYYY-MM-DD).
    edition_date: str = ""


class Signal(abc.ABC):
    """Abstract signal.

    Concrete signals subclass this and implement `compute`. Metadata
    (`id`, `name`, `category`, etc.) are class attributes, not
    constructor args, so the registry can enumerate them without
    instantiating.
    """

    # Stable identifier; matches the numbering in docs/signals/*.md
    id: str = ""
    # Display name for the section heading.
    name: str = ""
    # High-level category (`informed_flow`, `microstructure`,
    # `volume_liquidity`, `price_dynamics`, `event_catalyst`,
    # `cross_market`). Used for ordering + per-category composition.
    category: str = ""
    # One-line subtitle describing what the section shows.
    description: str = ""
    # v1 reliability band per SPEC-MARKET-SIGNALS § 7.
    reliability_band: Literal["high", "medium", "low"] = "medium"
    # Whether to suppress the section when there are no hits.
    hide_when_empty: bool = True

    @abc.abstractmethod
    def compute(self, context: SignalContext) -> list[SignalHit]:
        """Return every (wallet, market, window) detection."""

    @abc.abstractmethod
    def columns(self) -> list[ColumnSpec]:
        """Column list for the section's table. Static per signal."""

    def section_spec(self, hits: list[SignalHit]) -> SectionSpec:
        """Default section render. Signals can override for custom
        title / subtitle formatting based on aggregate hit stats.
        """
        return SectionSpec(
            signal_id=self.id,
            category=self.category,
            title=self.name,
            subtitle=self.description,
            columns=self.columns(),
            rows=[h.row for h in hits],
            reliability_band=self.reliability_band,
            hide_when_empty=self.hide_when_empty,
        )


# ── Report-level assembly ────────────────────────────────────────────


@dataclass
class DailyReport:
    """The fully-assembled newsletter payload.

    Built by the composer; consumed by `scripts/newsletter-daily.py`
    which hands it to the Tera template. Zero hardcoded copy in the
    template — every string the reader sees originates here.
    """

    # Cover / headline metadata.
    date: str
    window_start: datetime
    window_end: datetime
    edition_id: str

    # Summary stats shown above the sections.
    summary: list[tuple[str, str]] = field(default_factory=list)

    # Auto-generated headline sentence. Built from the top
    # `headline_fragment`s across all signals. See
    # composer.compose_headline().
    headline: str = ""

    # All signal sections, in display order (category then
    # signal-id lexical).
    sections: list[SectionSpec] = field(default_factory=list)

    # Glossary auto-built from the registry.
    glossary: list[tuple[str, str, str]] = field(default_factory=list)
    # (signal_name, reliability_band, description)

    # Raw-alert CSV rows (primary-data attachment).
    raw_alerts: list[dict[str, Any]] = field(default_factory=list)

    # Compliance-footer fields.
    footer_legal_name: str = ""
    footer_postal_address: str = ""

    # Data-source label for the header strip
    # (e.g. "data-api live feed", "detector rollup").
    source_label: str = ""


# ── Helpers ──────────────────────────────────────────────────────────


def _short_wallet(address: str) -> str:
    if not address or len(address) < 10:
        return address
    return f"{address[:6]}…{address[-4:]}"


def _money(value: Decimal | float | int) -> str:
    v = float(value)
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"


def _bps_signed(value: float | int) -> str:
    return f"{value:+,.0f} bps"


def _pct(value: float | int) -> str:
    return f"{value * 100:.0f}%"


def _wallet_list_html(contribs: list[tuple[str, float]]) -> str:
    """Render (address, amount) pairs as HTML.

    Used by market-level signals (OFI, clusters, velocity). Delegates
    per-wallet rendering to `_wallet_cell_html` so every wallet
    mention — standalone or in a contributor list — carries the
    same identicon + link treatment.
    """
    if not contribs:
        return "—"
    parts = [
        _wallet_cell_html(addr, amount=amount, show_amount=True)
        for addr, amount in contribs
    ]
    # Thin non-breaking separator between contributor chips so
    # identicons remain visually grouped with their address.
    return ' <span style="color:#ccc">·</span> '.join(parts)


# ── Visual system: category palette + badge helpers ─────────────────
#
# Three disciplined colors per category. fg = full-saturation text,
# bg = 5% tint for badge fill, bd = 15% tint for a hairline border.
# Tone-on-tone; no candy colors; newsroom/terminal palette.

# Category icons — PNG data-URIs generated at import time by
# `icons.py`. We used inline SVG initially; Gmail web (and several
# other mainstream clients) strips `<svg>` entirely, so the icons
# were silently invisible. PNG data-URIs are the universal-compat
# idiom.

CATEGORY_PALETTE: dict[str, dict[str, str]] = {
    "informed_flow":   {"fg": "#1e3a8a", "bg": "#eff6ff", "bd": "#bfdbfe",
                        "label": "Informed flow"},
    "microstructure":  {"fg": "#475569", "bg": "#f8fafc", "bd": "#cbd5e1",
                        "label": "Microstructure"},
    "volume_liquidity":{"fg": "#92400e", "bg": "#fffbeb", "bd": "#fde68a",
                        "label": "Volume / liquidity"},
    "price_dynamics":  {"fg": "#7e22ce", "bg": "#faf5ff", "bd": "#e9d5ff",
                        "label": "Price dynamics"},
    "event_catalyst":  {"fg": "#065f46", "bg": "#f0fdf4", "bd": "#bbf7d0",
                        "label": "Event catalyst"},
    "cross_market":    {"fg": "#0e7490", "bg": "#ecfeff", "bd": "#a5f3fc",
                        "label": "Cross-market"},
}
_DEFAULT_PALETTE = {"fg": "#374151", "bg": "#f9fafb", "bd": "#e5e7eb",
                    "label": "Signal"}


def category_palette(category: str) -> dict[str, str]:
    return CATEGORY_PALETTE.get(category, _DEFAULT_PALETTE)


def _badge_html(label: str, category: str, *, size: str = "sm") -> str:
    """Inline-styled badge with PNG-icon prepended — universal email.

    Icon `src` is mode-aware: `cid:…` for email (registered with the
    icons module so newsletter-daily can build MML `<#part>` blocks)
    and `data:image/png;base64,…` for PDF.
    """
    from .icons import category_icon_src

    p = category_palette(category)
    font_size = "10px" if size == "sm" else "11px"
    icon_src = category_icon_src(category, p["fg"])
    icon_html = (
        f'<img src="{icon_src}" width="12" height="12" alt="" '
        f'style="vertical-align:-2px;margin-right:4px;border:0">'
        if icon_src else ""
    )
    return (
        f'<span style="display:inline-block;padding:2px 8px 2px 6px;'
        f'border-radius:3px;font-size:{font_size};font-weight:600;'
        f'letter-spacing:0.02em;color:{p["fg"]};'
        f'background:{p["bg"]};'
        f'margin-right:4px;white-space:nowrap;'
        f'line-height:1.2">{icon_html}{label}</span>'
    )


def signal_badges_html(
    signals: list[tuple[str, str]],
) -> str:
    """Render a list of (signal_name, category) as a run of badges."""
    return "".join(_badge_html(name, cat) for name, cat in signals)


def category_badges_html(categories: list[str]) -> str:
    """Render a list of category keys as category-label badges."""
    parts = []
    for cat in categories:
        p = category_palette(cat)
        parts.append(_badge_html(p["label"], cat, size="md"))
    return "".join(parts)


# ── Blockie identicons (PNG data-URI, email-safe) ──────────────────


def _blockie_img(address: str) -> str:
    """Blockie as an `<img>` — `src` is `cid:…` for email (registered
    with the icons module) or `data:…;base64,…` for PDF. CID path
    keeps email size O(N) instead of O(N × occurrences).
    """
    if not address:
        return ""
    from .icons import blockie_src
    src = blockie_src(address)
    if not src:
        return ""
    return (
        f'<img src="{src}" width="14" height="14" alt="" '
        f'style="display:inline-block;vertical-align:-3px;'
        f'margin-right:4px;border:0;border-radius:2px">'
    )


def _volume_pill_html(volume_24h: float | None) -> str:
    """Tabular-nums volume pill — `$2.5M 24h` / `$125K 24h` / `—`."""
    if volume_24h is None:
        return ""
    try:
        v = float(volume_24h)
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    if v >= 1_000_000:
        fmt = f"${v / 1_000_000:.1f}M"
    elif v >= 1_000:
        fmt = f"${v / 1_000:.0f}K"
    else:
        fmt = f"${v:.0f}"
    return (
        f'<span style="display:inline-block;vertical-align:2px;'
        f'margin-left:6px;padding:1px 6px;border-radius:3px;'
        f'background:#f5f5f4;color:#57534e;font-size:10.5px;'
        f'font-weight:600;letter-spacing:0.02em;'
        f'font-variant-numeric:tabular-nums;white-space:nowrap;'
        f'line-height:1.3">{fmt} 24h</span>'
    )


def _market_state_html(
    *,
    last_trade_price: float | None,
    volume_24h: float | None,
    flagged: bool = True,
) -> str:
    """Donut + volume pill, sized to sit next to a market title.

    `last_trade_price` is the implied probability of YES ∈ [0, 1].
    `flagged` picks bronze (≥3× velocity / any signal fired) vs grey
    (informational only). Caller decides; every surface in the
    newsletter today is flagged-by-construction (the market surfaced
    because a signal fired), so the default is bronze.
    """
    from .icons import market_state_donut_src

    if last_trade_price is None and not volume_24h:
        return ""
    try:
        frac = float(last_trade_price) if last_trade_price is not None else 0.0
    except (TypeError, ValueError):
        frac = 0.0
    # Clip; Polymarket binary markets live in [0, 1] but occasionally
    # return a near-zero-or-one float the donut can still render fine.
    frac = max(0.0, min(1.0, frac))
    donut_src = market_state_donut_src(frac, flagged)
    donut_html = (
        f'<img src="{donut_src}" width="16" height="16" alt="" '
        f'style="display:inline-block;vertical-align:-3px;'
        f'margin-right:2px;border:0">'
        if donut_src else ""
    )
    pct_label = (
        f'<span style="margin-left:2px;color:#57534e;font-size:10.5px;'
        f'font-weight:600;font-variant-numeric:tabular-nums;'
        f'letter-spacing:0.02em;vertical-align:1px">'
        f'{int(round(frac * 100))}%</span>'
        if last_trade_price is not None else ""
    )
    pill = _volume_pill_html(volume_24h)
    return (
        f'<span style="white-space:nowrap;display:inline-block;'
        f'margin-left:8px">{donut_html}{pct_label}{pill}</span>'
    )


def _market_title_cell_html(
    title: str,
    url: str,
    *,
    last_trade_price: float | None,
    volume_24h: float | None,
    flagged: bool = True,
) -> str:
    """Market title link + donut + volume pill — the per-row cell.

    Used by `_section_to_payload` to replace the plain-text
    `market_title` column with a richer HTML cell in every signal
    section, plus by the cross-signal-markets + cross-market-wallets
    cards.
    """
    title_html = title or "—"
    if url:
        link = (
            f'<a href="{url}" style="color:#111;text-decoration:none;'
            f'font-weight:500">{title_html}</a>'
        )
    else:
        link = (
            f'<span style="color:#111;font-weight:500">{title_html}</span>'
        )
    state = _market_state_html(
        last_trade_price=last_trade_price,
        volume_24h=volume_24h,
        flagged=flagged,
    )
    return f'{link}{state}'


def _wallet_cell_html(
    address: str,
    *,
    amount: float | None = None,
    show_amount: bool = False,
) -> str:
    """Canonical wallet mention — blockie + shortform + profile link.

    Every wallet mention in the product flows through this helper so
    email, PDF, and any future surface stay in lock-step.
    """
    if not address:
        return "—"
    short = _short_wallet(address)
    blockie = _blockie_img(address)
    amount_tail = ""
    if show_amount and amount is not None:
        amount_tail = (
            f' <span style="color:#888;font-variant-numeric:tabular-nums">'
            f'({_money(amount)})</span>'
        )
    style = (
        "color:#1a5fb4;text-decoration:none;"
        "font-family:ui-monospace,'SF Mono',Menlo,monospace;"
        "font-size:12px"
    )
    return (
        f'{blockie}<a href="https://polymarket.com/profile/{address}" '
        f'style="{style}">{short}</a>{amount_tail}'
    )
