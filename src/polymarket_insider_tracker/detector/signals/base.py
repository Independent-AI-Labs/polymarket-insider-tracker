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
    """Render a list of (address, amount) pairs as HTML with
    clickable links to each wallet's Polymarket profile.

    Used by market-level signals (OFI, clusters, velocity) in the
    'Top contributors' column so every wallet mention in the email
    AND PDF is a single-click drill-down.
    """
    if not contribs:
        return "—"
    parts = []
    for addr, amount in contribs:
        short = _short_wallet(addr)
        money_fmt = _money(amount)
        parts.append(
            f'<a href="https://polymarket.com/profile/{addr}" '
            f'style="color:#1a5fb4;text-decoration:none;'
            f'font-family:monospace;font-size:12px">{short}</a> '
            f'<span style="color:#666">({money_fmt})</span>'
        )
    return ", ".join(parts)
