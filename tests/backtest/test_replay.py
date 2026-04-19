"""Unit tests for the replay capture iterator + detector heuristics."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_insider_tracker.backtest.replay import (
    MarketSnapshot,
    ReplayAssessment,
    WalletSnapshot,
    iter_capture,
    replay_capture,
    trade_event_to_record,
)
from polymarket_insider_tracker.ingestor.models import TradeEvent


def _trade(**overrides) -> TradeEvent:
    base = dict(
        market_id="0xmkt",
        trade_id="0xtx1",
        wallet_address="0xwallet",
        side="BUY",
        outcome="Yes",
        outcome_index=0,
        price=Decimal("0.1"),
        size=Decimal("20000"),   # notional 2000
        timestamp=datetime(2026, 4, 19, 13, tzinfo=UTC),
        asset_id="0xasset",
        market_slug="mkt",
        event_slug="event",
        event_title="Event",
        trader_name="",
        trader_pseudonym="",
    )
    base.update(overrides)
    return TradeEvent(**base)


class TestIterCapture:
    def test_round_trips_through_writer(self, tmp_path: Path) -> None:
        event = _trade()
        path = tmp_path / "capture.jsonl"
        import json
        with path.open("w") as fh:
            fh.write(json.dumps(trade_event_to_record(event)) + "\n")

        events = list(iter_capture(path))
        assert len(events) == 1
        rehydrated = events[0]
        assert rehydrated.market_id == event.market_id
        assert rehydrated.price == event.price
        assert rehydrated.timestamp == event.timestamp

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        event = _trade()
        path = tmp_path / "capture.jsonl"
        import json
        with path.open("w") as fh:
            fh.write("\n")
            fh.write(json.dumps(trade_event_to_record(event)) + "\n")
            fh.write("   \n")

        events = list(iter_capture(path))
        assert len(events) == 1

    def test_raises_on_malformed_line(self, tmp_path: Path) -> None:
        path = tmp_path / "capture.jsonl"
        path.write_text("not-json\n")
        with pytest.raises(ValueError, match="invalid JSON"):
            list(iter_capture(path))


def _make_wallet_resolver(is_fresh: bool, nonce: int = 2):
    async def _resolve(address: str, at: datetime) -> WalletSnapshot:
        return WalletSnapshot(
            address=address,
            nonce=nonce,
            first_seen_at=None,
            is_fresh=is_fresh,
        )
    return _resolve


def _make_market_resolver(daily_volume: Decimal | None, book_depth: Decimal | None = None):
    async def _resolve(market_id: str, at: datetime) -> MarketSnapshot:
        return MarketSnapshot(
            market_id=market_id,
            daily_volume=daily_volume,
            book_depth=book_depth,
        )
    return _resolve


@pytest.mark.asyncio
class TestReplayCapture:
    async def test_fresh_wallet_signal_triggers_when_nonce_low(
        self, tmp_path: Path
    ) -> None:
        import json
        path = tmp_path / "cap.jsonl"
        with path.open("w") as fh:
            fh.write(json.dumps(trade_event_to_record(_trade())) + "\n")

        assessments, stats = await replay_capture(
            path,
            resolve_wallet=_make_wallet_resolver(is_fresh=True),
            resolve_market=_make_market_resolver(daily_volume=Decimal("1000000")),
        )
        assert stats.trades_processed == 1
        assert stats.assessments_emitted == 1
        assert "fresh_wallet" in assessments[0].signals_triggered

    async def test_no_signals_produces_no_assessment(self, tmp_path: Path) -> None:
        import json
        path = tmp_path / "cap.jsonl"
        with path.open("w") as fh:
            fh.write(json.dumps(trade_event_to_record(_trade())) + "\n")

        assessments, stats = await replay_capture(
            path,
            resolve_wallet=_make_wallet_resolver(is_fresh=False, nonce=500),
            resolve_market=_make_market_resolver(daily_volume=Decimal("100000000")),
        )
        assert stats.trades_processed == 1
        assert stats.assessments_emitted == 0
        assert assessments == []

    async def test_size_anomaly_fires_on_volume_impact(self, tmp_path: Path) -> None:
        import json
        # Notional 2000 against daily volume 50000 = 4% > 2% threshold
        path = tmp_path / "cap.jsonl"
        with path.open("w") as fh:
            fh.write(json.dumps(trade_event_to_record(_trade())) + "\n")

        assessments, _ = await replay_capture(
            path,
            resolve_wallet=_make_wallet_resolver(is_fresh=False, nonce=500),
            resolve_market=_make_market_resolver(daily_volume=Decimal("50000")),
        )
        # Daily volume is at the niche threshold boundary ($50k) so the
        # niche signal should not fire (< 50_000 is strict).
        assert len(assessments) == 1
        assert "size_anomaly" in assessments[0].signals_triggered
        assert "niche_market" not in assessments[0].signals_triggered

    async def test_niche_market_fires_below_50k(self, tmp_path: Path) -> None:
        import json
        path = tmp_path / "cap.jsonl"
        with path.open("w") as fh:
            fh.write(json.dumps(trade_event_to_record(_trade())) + "\n")

        assessments, _ = await replay_capture(
            path,
            resolve_wallet=_make_wallet_resolver(is_fresh=False, nonce=500),
            resolve_market=_make_market_resolver(daily_volume=Decimal("25000")),
        )
        assert len(assessments) == 1
        assert "niche_market" in assessments[0].signals_triggered

    async def test_weighted_score_caps_at_one(self, tmp_path: Path) -> None:
        import json
        path = tmp_path / "cap.jsonl"
        with path.open("w") as fh:
            fh.write(json.dumps(trade_event_to_record(_trade())) + "\n")

        # All three signals fire: fresh + size anomaly + niche.
        assessments, _ = await replay_capture(
            path,
            resolve_wallet=_make_wallet_resolver(is_fresh=True, nonce=2),
            resolve_market=_make_market_resolver(daily_volume=Decimal("25000")),
        )
        assert len(assessments) == 1
        a = assessments[0]
        assert set(a.signals_triggered) == {"fresh_wallet", "size_anomaly", "niche_market"}
        # 0.4 + 0.35 + 0.25 = 1.0; * 1.2 * 1.3 = 1.56 -> capped to 1.0
        assert a.weighted_score == pytest.approx(1.0)
