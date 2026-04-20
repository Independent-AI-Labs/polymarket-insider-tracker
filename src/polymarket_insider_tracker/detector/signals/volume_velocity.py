"""Signal 03-A — Volume velocity.

Spec: docs/signals/03-volume-liquidity.md § 03-A.

24h volume divided by the market's all-time daily baseline.
Flags markets whose activity has suddenly outrun history.

Market-level signal — gamma-api volume + startDate fields only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from .base import (
    ColumnSpec,
    Signal,
    SignalContext,
    SignalHit,
    _money,
)

MIN_MULTIPLE = 3.0
MIN_DAYS_ACTIVE = 3.0
MIN_VOLUME_24H = 50_000


class VolumeVelocitySignal(Signal):
    id = "03-A-volume-velocity"
    name = "Volume spikes"
    category = "volume_liquidity"
    description = (
        f"Markets whose last-24 h volume is ≥ {MIN_MULTIPLE:.0f}× "
        "their all-time daily average. Filters out newly-created "
        f"markets (< {MIN_DAYS_ACTIVE:.0f} d) whose baseline is "
        "not yet stable."
    )
    reliability_band = "medium"
    hide_when_empty = True

    def columns(self) -> list[ColumnSpec]:
        return [
            ColumnSpec("market_title", "Market", "left", "text",
                       link_field="market_url", width_hint="45%"),
            ColumnSpec("volume_24h_fmt", "24 h vol", "right", "money"),
            ColumnSpec("baseline_fmt", "Baseline/d", "right", "money"),
            ColumnSpec("multiple_fmt", "Multiple", "right", "text"),
            ColumnSpec("days_active_fmt", "Age", "right", "duration"),
        ]

    def compute(self, context: SignalContext) -> list[SignalHit]:
        if not context.market_meta:
            return []

        now = context.window_end or datetime.now(UTC)
        hits: list[SignalHit] = []
        for cid, m in context.market_meta.items():
            vol_24h = Decimal(str(m.get("volume24hr", 0) or 0))
            vol_total = Decimal(str(m.get("volumeNum", 0) or 0))
            if vol_24h < MIN_VOLUME_24H:
                continue
            start_raw = str(m.get("startDate", "") or m.get("startDateIso", ""))
            if not start_raw:
                continue
            try:
                start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            days_active = max((now - start_dt).total_seconds() / 86400, 0.1)
            if days_active < MIN_DAYS_ACTIVE or vol_total <= 0:
                continue
            baseline = vol_total / Decimal(str(days_active))
            if baseline <= 0:
                continue
            multiple = float(vol_24h / baseline)
            if multiple < MIN_MULTIPLE:
                continue
            hits.append(
                SignalHit(
                    signal_id=self.id,
                    wallet_address="",
                    market_id=cid,
                    market_title=str(m.get("question", "")),
                    event_slug=str(m.get("slug", "")),
                    score=min(1.0, 0.4 + min((multiple - MIN_MULTIPLE) / 10, 0.6)),
                    row={
                        "market_title": str(m.get("question", "")),
                        "market_url": f"https://polymarket.com/event/{m.get('slug', '')}",
                        "volume_24h": float(vol_24h),
                        "volume_24h_fmt": _money(vol_24h),
                        "baseline": float(baseline),
                        "baseline_fmt": _money(baseline),
                        "multiple": multiple,
                        "multiple_fmt": f"{multiple:.1f}×",
                        "days_active": days_active,
                        "days_active_fmt": f"{days_active:.0f}d",
                    },
                )
            )

        hits.sort(key=lambda h: h.row["multiple"], reverse=True)
        hits = hits[:5]

        if hits:
            top = hits[0]
            fragment = (
                f"volume {top.row['multiple_fmt']} baseline on "
                f"<em>{top.market_title}</em>"
            )
            hits[0] = SignalHit(**{**hits[0].__dict__, "headline_fragment": fragment})
        return hits
