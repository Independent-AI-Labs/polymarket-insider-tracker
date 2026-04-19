"""End-to-end test for `python -m polymarket_insider_tracker.backtest`.

Feeds a synthetic jsonl capture through the CLI with its HTTP
resolvers faked out, then asserts detector_metrics rows land in
SQLite. Catches drift in the capture → replay → classify →
aggregate → persist pipeline as a single integration check.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from polymarket_insider_tracker.backtest import __main__ as cli
from polymarket_insider_tracker.backtest.replay import (
    MarketSnapshot,
    WalletSnapshot,
    trade_event_to_record,
)
from polymarket_insider_tracker.ingestor.models import TradeEvent
from polymarket_insider_tracker.storage.models import Base, DetectorMetricsModel


def _trade(wallet: str, market: str, notional_usdc: int, tx: str) -> TradeEvent:
    return TradeEvent(
        market_id=market,
        trade_id=tx,
        wallet_address=wallet,
        side="BUY",
        outcome="Yes",
        outcome_index=0,
        price=Decimal("0.2"),
        size=Decimal(notional_usdc) / Decimal("0.2"),
        timestamp=datetime(2026, 4, 19, 13, tzinfo=UTC),
        asset_id="0xasset",
        market_slug="mkt",
        event_slug="evt",
        event_title="Event",
        trader_name="",
        trader_pseudonym="",
    )


class _FakeMarketResolver:
    """Canned MarketSnapshots for the test fixture."""
    def __init__(self, snaps):
        self._cache = dict(snaps)
    async def __call__(self, market_id, at):
        return self._cache.get(market_id.lower())


class _FakeWalletResolver:
    def __init__(self, snaps):
        self._cache = dict(snaps)
    async def __call__(self, address, at):
        return self._cache.get(address.lower())


@pytest.mark.asyncio
async def test_cli_run_writes_detector_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Build a 3-trade capture: one fresh-wallet + size + niche
    # composite, one mature wallet in a mainstream market (silent),
    # one fresh wallet + small trade (silent via min_trade_size).
    capture = tmp_path / "synth.jsonl"
    trades = [
        _trade(wallet="0xaaaa1", market="0xmkt1", notional_usdc=15000, tx="0xtxhot"),
        _trade(wallet="0xbbbb2", market="0xmkt2", notional_usdc=50000, tx="0xtxold"),
        _trade(wallet="0xcccc3", market="0xmkt1", notional_usdc=200, tx="0xtxsmall"),
    ]
    with capture.open("w") as fh:
        for t in trades:
            fh.write(json.dumps(trade_event_to_record(t)) + "\n")

    # In-memory SQLite — monkeypatch get_settings to return a
    # stub whose database.url points at a shared SQLite file.
    db_path = tmp_path / "metrics.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    class _StubPolygon:
        rpc_url = "http://stub-polygon.invalid"

    class _StubSettings:
        class database:
            pass
        polygon = _StubPolygon()

    _StubSettings.database.url = url
    monkeypatch.setattr(cli, "get_settings", lambda: _StubSettings())

    # Replace the real Gamma + Polygon resolvers with fakes so the
    # test doesn't hit the network.
    wallet_snaps = {
        "0xaaaa1": WalletSnapshot("0xaaaa1", nonce=2, first_seen_at=None, is_fresh=True),
        "0xbbbb2": WalletSnapshot("0xbbbb2", nonce=800, first_seen_at=None, is_fresh=False),
        "0xcccc3": WalletSnapshot("0xcccc3", nonce=1, first_seen_at=None, is_fresh=True),
    }
    market_snaps = {
        "0xmkt1": MarketSnapshot(
            market_id="0xmkt1",
            daily_volume=Decimal("40000"),  # niche
            book_depth=None,
            category="other",
        ),
        "0xmkt2": MarketSnapshot(
            market_id="0xmkt2",
            daily_volume=Decimal("5000000"),  # mainstream
            book_depth=None,
            category="politics",
        ),
    }

    class _StubMarket(cli._GammaMarketResolver):
        def __init__(self):
            self._cache = dict(market_snaps)
        async def __call__(self, mid, at):
            return self._cache.get(mid.lower())

    class _StubWallet(cli._PolygonWalletResolver):
        def __init__(self):
            self._cache = dict(wallet_snaps)
        async def __call__(self, addr, at):
            return self._cache.get(addr.lower())

    monkeypatch.setattr(cli, "_GammaMarketResolver", lambda *a, **kw: _StubMarket())
    monkeypatch.setattr(cli, "_PolygonWalletResolver", lambda *a, **kw: _StubWallet())

    rc = await cli.run(capture=capture, window_days=1, max_trades=None)
    assert rc == 0

    # Verify detector_metrics rows landed.
    engine = create_async_engine(url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    from sqlalchemy import select

    async with factory() as session:
        result = await session.execute(select(DetectorMetricsModel))
        rows = list(result.scalars().all())
    await engine.dispose()

    assert rows, "expected detector_metrics rows after CLI run"
    signals = {r.signal for r in rows}
    # Composite hot trade fires fresh_wallet + size_anomaly + niche_market
    # (plus the combined row emitted by aggregate_metrics).
    assert {"fresh_wallet", "size_anomaly", "niche_market", "combined"}.issubset(signals)
    fresh = next(r for r in rows if r.signal == "fresh_wallet")
    assert fresh.alerts_total == 1
