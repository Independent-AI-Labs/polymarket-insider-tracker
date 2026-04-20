"""Signal 03-A — Volume velocity.

Spec: docs/signals/03-volume-liquidity.md § 03-A.

24h volume divided by the market's all-time daily baseline.
Market-level signal — now also surfaces the top contributing
wallets so the reader can drill.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .base import (
    ColumnSpec,
    Signal,
    SignalContext,
    SignalHit,
    _money,
    _short_wallet,
    _wallet_list_html,
)
from .gates import DEFAULT_GATES, GateConfig, passes_all


class VolumeVelocitySignal(Signal):
    id = "03-A-volume-velocity"
    name = "Volume spikes"
    category = "volume_liquidity"
    reliability_band = "medium"
    hide_when_empty = True

    def __init__(
        self,
        *,
        min_multiple: float = 3.0,
        min_days_active: float = 3.0,
        min_volume_24h: float = 50_000.0,
        top_contributors: int = 3,
        top_n: int = 5,
        gate_config: GateConfig | None = None,
    ) -> None:
        self.min_multiple = min_multiple
        self.min_days_active = min_days_active
        self.min_volume_24h = min_volume_24h
        self.top_contributors = top_contributors
        self.top_n = top_n
        self.gates = gate_config or DEFAULT_GATES
        self.description = (
            f"Markets whose last-24 h volume is ≥ {self.min_multiple:.0f}× "
            "their all-time daily average. Filters out newly-created "
            f"markets (< {self.min_days_active:.0f} d) whose baseline "
            "is not yet stable, and price-extreme markets."
        )

    def columns(self) -> list[ColumnSpec]:
        return [
            ColumnSpec("market_title", "Market", "left", "text",
                       link_field="market_url", width_hint="34%"),
            ColumnSpec("volume_24h_fmt", "24 h vol", "right", "money"),
            ColumnSpec("baseline_fmt", "Baseline/d", "right", "money"),
            ColumnSpec("multiple_fmt", "Multiple", "right", "text"),
            ColumnSpec("days_active_fmt", "Age", "right", "duration"),
            ColumnSpec("top_wallets_fmt", "Top contributors", "left", "html"),
        ]

    def compute(self, context: SignalContext) -> list[SignalHit]:
        if not context.market_meta:
            return []

        # Aggregate trades per (market, wallet) for contributor lists.
        by_wallet_market: dict[tuple[str, str], Decimal] = {}
        for t in context.trades:
            mid = str(t.get("conditionId", "")).lower()
            wallet = str(t.get("proxyWallet", "")).lower()
            if not mid or not wallet:
                continue
            key = (mid, wallet)
            n = Decimal(str(t.get("size", 0))) * Decimal(str(t.get("price", 0)))
            by_wallet_market[key] = by_wallet_market.get(key, Decimal("0")) + n

        now = context.window_end or datetime.now(UTC)
        hits: list[SignalHit] = []
        for cid, m in context.market_meta.items():
            mid = cid.lower()
            if not passes_all(
                m,
                self.gates,
                require_price=True,
                require_time=True,
                require_lifespan=True,
                require_novelty_skip=True,
                require_liquidity=False,
            ):
                continue
            vol_24h = Decimal(str(m.get("volume24hr", 0) or 0))
            vol_total = Decimal(str(m.get("volumeNum", 0) or 0))
            if vol_24h < self.min_volume_24h:
                continue
            start_raw = str(m.get("startDate", "") or m.get("startDateIso", ""))
            if not start_raw:
                continue
            try:
                start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            days_active = max((now - start_dt).total_seconds() / 86400, 0.1)
            if days_active < self.min_days_active or vol_total <= 0:
                continue
            baseline = vol_total / Decimal(str(days_active))
            if baseline <= 0:
                continue
            multiple = float(vol_24h / baseline)
            if multiple < self.min_multiple:
                continue

            contribs = [
                (wallet, amount)
                for (m2, wallet), amount in by_wallet_market.items()
                if m2 == mid
            ]
            contribs.sort(key=lambda p: p[1], reverse=True)
            top_contribs = contribs[: self.top_contributors]
            top_wallets_fmt = _wallet_list_html(
                [(w, float(a)) for w, a in top_contribs]
            )

            hits.append(
                SignalHit(
                    signal_id=self.id,
                    wallet_address="",
                    market_id=mid,
                    market_title=str(m.get("question", "")),
                    event_slug=str(m.get("slug", "")),
                    score=min(1.0, 0.4 + min((multiple - self.min_multiple) / 10, 0.6)),
                    row={
                        "market_id": mid,
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
                        "top_wallets": [
                            {"address": w, "amount": float(a)}
                            for w, a in top_contribs
                        ],
                        "top_wallets_fmt": top_wallets_fmt,
                    },
                )
            )

        hits.sort(key=lambda h: h.row["multiple"], reverse=True)
        hits = hits[: self.top_n]

        if hits:
            top = hits[0]
            fragment = (
                f"volume {top.row['multiple_fmt']} baseline on "
                f"<em>{top.market_title}</em>"
            )
            hits[0] = SignalHit(**{**hits[0].__dict__, "headline_fragment": fragment})
        return hits
