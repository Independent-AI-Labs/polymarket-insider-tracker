"""Self-tests for the Scenario harness.

These exercise the builder methods, the replay bridge, the rollup
aggregator, and the golden-file scrubbing — all without touching
himalaya. Phase 1.5.1 / 1.5.2 from IMPLEMENTATION-TODOS.md.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_insider_tracker.backtest.outcomes import MarketOutcome, OutcomeLabel
from polymarket_insider_tracker.backtest.replay import MarketSnapshot, WalletSnapshot
from polymarket_insider_tracker.ingestor.models import TradeEvent
from tests.scenarios._harness import Scenario, _scrub


def _trade(
    *,
    wallet: str = "0xaaaa",
    market: str = "0xmarket",
    price: str = "0.1",
    size: str = "10000",
    side: str = "BUY",
    tx: str = "0xtx1",
) -> TradeEvent:
    return TradeEvent(
        market_id=market,
        trade_id=tx,
        wallet_address=wallet,
        side=side,  # type: ignore[arg-type]
        outcome="Yes",
        outcome_index=0,
        price=Decimal(price),
        size=Decimal(size),
        timestamp=datetime(2026, 4, 19, 13, tzinfo=UTC),
        asset_id="0xasset",
        market_slug="mkt",
        event_slug="event",
        event_title="Event",
        trader_name="",
        trader_pseudonym="",
    )


class TestBuilders:
    def test_given_trades_populates_list(self, tmp_path):
        scenario = Scenario(
            name="t", himalaya_binary="himalaya", tmp_dir=tmp_path
        ).given_trades([_trade()])
        assert len(scenario._trades) == 1

    def test_with_snapshots_normalises_address(self, tmp_path):
        scenario = (
            Scenario(name="t", himalaya_binary="himalaya", tmp_dir=tmp_path)
            .with_wallet_snapshots(
                {
                    "0xAAA": WalletSnapshot(
                        address="0xAAA", nonce=1, first_seen_at=None, is_fresh=True
                    )
                }
            )
        )
        assert "0xaaa" in scenario._wallets
        assert scenario._wallets["0xaaa"].is_fresh is True

    def test_builders_return_self(self, tmp_path):
        scenario = Scenario(name="t", himalaya_binary="h", tmp_dir=tmp_path)
        assert scenario.given_trades([]) is scenario
        assert scenario.with_wallet_snapshots({}) is scenario
        assert scenario.with_market_snapshots({}) is scenario
        assert scenario.with_market_outcomes({}) is scenario


@pytest.mark.asyncio
class TestReplayBridge:
    async def test_fresh_wallet_signal_propagates(self, tmp_path):
        scenario = (
            Scenario(name="t", himalaya_binary="h", tmp_dir=tmp_path)
            .given_trades([_trade(size="20000")])
            .with_wallet_snapshots(
                {
                    "0xaaaa": WalletSnapshot(
                        address="0xaaaa",
                        nonce=2,
                        first_seen_at=None,
                        is_fresh=True,
                    )
                }
            )
            .with_market_snapshots(
                {
                    "0xmarket": MarketSnapshot(
                        market_id="0xmarket",
                        daily_volume=Decimal("1000000"),
                        book_depth=None,
                    )
                }
            )
        )
        assessments = await scenario.when_replayed()
        assert len(assessments) == 1
        assert "fresh_wallet" in assessments[0].signals_triggered

    async def test_no_snapshot_falls_back_to_non_fresh(self, tmp_path):
        scenario = (
            Scenario(name="t", himalaya_binary="h", tmp_dir=tmp_path)
            .given_trades([_trade()])
            # Deliberately no wallet snapshot — defaults to nonce=500.
            .with_market_snapshots(
                {
                    "0xmarket": MarketSnapshot(
                        market_id="0xmarket",
                        daily_volume=Decimal("10000000"),
                        book_depth=None,
                    )
                }
            )
        )
        assessments = await scenario.when_replayed()
        assert assessments == []


@pytest.mark.asyncio
class TestClassifyAndAggregate:
    async def _prep(self, tmp_path) -> Scenario:
        return (
            Scenario(name="t", himalaya_binary="h", tmp_dir=tmp_path)
            .given_trades(
                [
                    _trade(wallet="0xaaa1", tx="0xtx1", size="20000"),
                    _trade(wallet="0xaaa2", tx="0xtx2", size="20000"),
                ]
            )
            .with_wallet_snapshots(
                {
                    "0xaaa1": WalletSnapshot(
                        address="0xaaa1",
                        nonce=2,
                        first_seen_at=None,
                        is_fresh=True,
                    ),
                    "0xaaa2": WalletSnapshot(
                        address="0xaaa2",
                        nonce=3,
                        first_seen_at=None,
                        is_fresh=True,
                    ),
                }
            )
            .with_market_snapshots(
                {
                    "0xmarket": MarketSnapshot(
                        market_id="0xmarket",
                        daily_volume=Decimal("1000000"),
                        book_depth=None,
                    )
                }
            )
        )

    async def test_aggregate_rollup_groups_correctly(self, tmp_path):
        scenario = await self._prep(tmp_path)
        await scenario.when_replayed()
        rollup = scenario.aggregate_rollup(day=date(2026, 4, 19))
        key = ("2026-04-19", "0xmarket", "fresh_wallet")
        assert rollup[key]["alert_count"] == 2
        assert rollup[key]["unique_wallets"] == 2
        assert rollup[key]["total_notional"] == Decimal("20000") * 2 * Decimal("0.1")

    async def test_classify_outcomes_uses_seeded_outcome(self, tmp_path):
        scenario = await self._prep(tmp_path)
        scenario.with_market_outcomes(
            {
                "0xmarket": MarketOutcome(
                    market_id="0xmarket",
                    reference_price=Decimal("0.1"),
                    final_price=Decimal("1.0"),
                    is_resolved=True,
                )
            }
        )
        await scenario.when_replayed()
        outcomes = scenario.classify_outcomes()
        assert len(outcomes) == 2
        assert all(o.label == OutcomeLabel.HIT for o in outcomes)


class TestScrubber:
    def test_uuid_replaced(self):
        text = "id=f47ac10b-58cc-4372-a567-0e02b2c3d479 done"
        assert _scrub(text) == "id=<UUID> done"

    def test_iso_timestamp_replaced(self):
        text = "at 2026-04-19T13:00:00+00:00 finished"
        scrubbed = _scrub(text)
        assert "2026-04-19" not in scrubbed
        assert "<TS>" in scrubbed

    def test_tmp_path_replaced(self):
        text = "file /tmp/pytest-of-user/pytest-23/x.yaml ok"
        assert "<TMP>" in _scrub(text)

    def test_targets_filename_replaced(self):
        text = "/tmp/x/polymarket-targets-a1b2c3d4.yaml loaded"
        scrubbed = _scrub(text)
        assert "polymarket-targets-" not in scrubbed


class TestGoldenAssertion:
    def test_matching_golden_passes(self, tmp_path: Path):
        golden = tmp_path / "g.html"
        golden.write_text("hello world", encoding="utf-8")
        Scenario.assert_matches_golden("hello world", golden)

    def test_update_overwrites(self, tmp_path: Path):
        golden = tmp_path / "g.html"
        golden.write_text("old", encoding="utf-8")
        Scenario.assert_matches_golden("new", golden, update=True)
        assert golden.read_text() == "new"

    def test_mismatch_raises_with_diff(self, tmp_path: Path):
        golden = tmp_path / "g.html"
        golden.write_text("hello old world", encoding="utf-8")
        with pytest.raises(AssertionError, match="does not match golden"):
            Scenario.assert_matches_golden("hello new world", golden)

    def test_missing_golden_writes_and_passes(self, tmp_path: Path):
        golden = tmp_path / "g.html"
        Scenario.assert_matches_golden("first run", golden)
        assert golden.read_text() == "first run"


class TestContainsAll:
    def test_all_present_passes(self):
        Scenario.assert_contains_all("hello world foo", ["hello", "foo"])

    def test_missing_raises_with_names(self):
        with pytest.raises(AssertionError, match=r"\['foo'\]"):
            Scenario.assert_contains_all("hello world", ["hello", "foo"])
