"""Unit tests for the pure backtest helpers (outcomes + metrics).

Covers the pieces that don't need Redis / DB / RPC: classifying an
assessment against a market outcome and aggregating classified
outcomes into metrics rows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from polymarket_insider_tracker.backtest.metrics import (
    COMBINED_SIGNAL,
    KNOWN_SIGNALS,
    MetricsWindow,
    aggregate_metrics,
)
from polymarket_insider_tracker.backtest.outcomes import (
    DEFAULT_MOVE_THRESHOLD_BPS,
    AssessmentOutcome,
    MarketOutcome,
    OutcomeLabel,
    classify_assessment,
)


def _outcome(ref: str, final: str, resolved: bool = True) -> MarketOutcome:
    return MarketOutcome(
        market_id="0xmkt",
        reference_price=Decimal(ref),
        final_price=Decimal(final),
        is_resolved=resolved,
    )


class TestClassifyAssessment:
    """Every branch of the hit/miss/pending decision."""

    def _classify(self, **kwargs):
        base = {
            "assessment_id": "a-1",
            "wallet_address": "0xwallet",
            "market_id": "0xmkt",
            "side": "BUY",
            "outcome_index": 0,
            "signals_triggered": ("fresh_wallet",),
            "weighted_score": 0.65,
        }
        base.update(kwargs)
        return classify_assessment(**base)

    def test_buy_signaled_side_resolves_yes_is_hit(self):
        res = self._classify(side="BUY", outcome=_outcome("0.20", "1.00"))
        assert res.label == OutcomeLabel.HIT
        assert res.move_bps == 8000

    def test_buy_signaled_side_resolves_no_is_miss(self):
        res = self._classify(side="BUY", outcome=_outcome("0.20", "0.00"))
        assert res.label == OutcomeLabel.MISS
        assert res.move_bps == -2000

    def test_sell_signaled_side_moves_down_is_hit(self):
        res = self._classify(side="SELL", outcome=_outcome("0.80", "0.05"))
        assert res.label == OutcomeLabel.HIT
        assert res.move_bps == -7500

    def test_sell_signaled_side_moves_up_is_miss(self):
        res = self._classify(side="SELL", outcome=_outcome("0.30", "0.90"))
        assert res.label == OutcomeLabel.MISS

    def test_unresolved_flat_price_is_pending(self):
        res = self._classify(
            side="BUY",
            outcome=_outcome("0.30", "0.31", resolved=False),
        )
        assert res.label == OutcomeLabel.PENDING

    def test_custom_threshold_reclassifies(self):
        # 200 bps move is a HIT at threshold 100, MISS/hit otherwise.
        res = self._classify(
            side="BUY",
            outcome=_outcome("0.30", "0.32", resolved=True),
            move_threshold_bps=100,
        )
        assert res.label == OutcomeLabel.HIT

    def test_resolved_below_threshold_is_miss_not_pending(self):
        # Resolved market that didn't move materially must count as MISS
        # per the "alert didn't pay" operator perspective.
        res = self._classify(
            side="BUY",
            outcome=_outcome("0.50", "0.51", resolved=True),
        )
        assert res.label == OutcomeLabel.MISS

    def test_default_threshold_matches_docstring(self):
        # Sanity: the exported default matches what we're testing against.
        assert DEFAULT_MOVE_THRESHOLD_BPS == 500


class TestAggregateMetrics:
    """Per-signal roll-up + combined row."""

    def _outcome_row(
        self,
        signals: tuple[str, ...],
        label: OutcomeLabel,
        move_bps: int = 1000,
    ) -> AssessmentOutcome:
        return AssessmentOutcome(
            assessment_id=f"a-{label}-{','.join(signals)}",
            wallet_address="0x",
            market_id="0xm",
            side="BUY",
            outcome_index=0,
            reference_price=Decimal("0.2"),
            final_price=Decimal("0.3"),
            move_bps=move_bps,
            label=label,
            signals_triggered=signals,
            weighted_score=0.7,
        )

    def _window(self) -> MetricsWindow:
        return MetricsWindow(
            start=datetime(2026, 4, 1, tzinfo=UTC),
            end=datetime(2026, 5, 1, tzinfo=UTC),
        )

    def test_emits_row_per_known_signal_plus_combined(self):
        rows = aggregate_metrics([], self._window())
        assert [r.signal for r in rows] == [*KNOWN_SIGNALS, COMBINED_SIGNAL]
        for r in rows:
            assert r.alerts_total == 0
            assert r.precision is None
            assert r.pnl_uplift_bps is None

    def test_precision_rounds_to_four_decimals(self):
        outs = [
            self._outcome_row(("fresh_wallet",), OutcomeLabel.HIT),
            self._outcome_row(("fresh_wallet",), OutcomeLabel.HIT),
            self._outcome_row(("fresh_wallet",), OutcomeLabel.MISS),
        ]
        rows = aggregate_metrics(outs, self._window())
        fresh = next(r for r in rows if r.signal == "fresh_wallet")
        assert fresh.alerts_total == 3
        assert fresh.hits == 2
        assert fresh.misses == 1
        assert fresh.precision == Decimal("0.6667")

    def test_pending_excluded_from_precision_denominator(self):
        outs = [
            self._outcome_row(("fresh_wallet",), OutcomeLabel.HIT),
            self._outcome_row(("fresh_wallet",), OutcomeLabel.PENDING),
            self._outcome_row(("fresh_wallet",), OutcomeLabel.PENDING),
        ]
        rows = aggregate_metrics(outs, self._window())
        fresh = next(r for r in rows if r.signal == "fresh_wallet")
        assert fresh.precision == Decimal("1.0000")
        assert fresh.pending == 2

    def test_pnl_uplift_averages_signed_move(self):
        outs = [
            self._outcome_row(("size_anomaly",), OutcomeLabel.HIT, move_bps=2000),
            self._outcome_row(("size_anomaly",), OutcomeLabel.MISS, move_bps=-500),
            # Pending outcomes excluded from uplift averaging
            self._outcome_row(("size_anomaly",), OutcomeLabel.PENDING, move_bps=9999),
        ]
        rows = aggregate_metrics(outs, self._window())
        size = next(r for r in rows if r.signal == "size_anomaly")
        assert size.pnl_uplift_bps == 750  # (2000 + -500) / 2

    def test_combined_row_counts_any_signal(self):
        outs = [
            self._outcome_row(("fresh_wallet",), OutcomeLabel.HIT),
            self._outcome_row(("size_anomaly",), OutcomeLabel.HIT),
            self._outcome_row(("fresh_wallet", "size_anomaly"), OutcomeLabel.MISS),
        ]
        rows = aggregate_metrics(outs, self._window())
        combined = next(r for r in rows if r.signal == COMBINED_SIGNAL)
        assert combined.alerts_total == 3
        assert combined.hits == 2
        assert combined.misses == 1

    def test_empty_signal_tuple_not_included_in_combined(self):
        outs = [self._outcome_row((), OutcomeLabel.HIT)]
        rows = aggregate_metrics(outs, self._window())
        combined = next(r for r in rows if r.signal == COMBINED_SIGNAL)
        assert combined.alerts_total == 0

    def test_window_fields_propagate(self):
        window = self._window()
        rows = aggregate_metrics([], window)
        for r in rows:
            assert r.window_start == window.start
            assert r.window_end == window.end
