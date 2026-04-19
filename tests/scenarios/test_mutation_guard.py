"""Mutation-guard suite — Phase 8 of IMPLEMENTATION-TODOS.

Each scenario documented a specific detector threshold it's
sensitive to. This file nudges each threshold via
`monkeypatch.setattr` and asserts the corresponding scenario stops
alerting — i.e. the assertion was actually *checking* the signal
rather than passing by accident.

If any of these mutations silently pass, the scenario test
needs tightening.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from polymarket_insider_tracker.backtest import replay as replay_mod
from polymarket_insider_tracker.backtest.replay import (
    MarketSnapshot,
    WalletSnapshot,
    replay_capture,
    trade_event_to_record,
)
from polymarket_insider_tracker.ingestor.models import TradeEvent


def _trade(wallet: str = "0xaaaa") -> TradeEvent:
    return TradeEvent(
        market_id="0xmarket",
        trade_id="0xtx1",
        wallet_address=wallet,
        side="BUY",
        outcome="Yes",
        outcome_index=0,
        price=Decimal("0.1"),
        size=Decimal("200000"),     # notional $20k
        timestamp=datetime(2026, 4, 19, 13, tzinfo=UTC),
        asset_id="0xasset",
        market_slug="mkt",
        event_slug="event",
        event_title="Event",
        trader_name="",
        trader_pseudonym="",
    )


async def _wallet_fresh(address: str, at):
    return WalletSnapshot(
        address=address, nonce=2, first_seen_at=None, is_fresh=True
    )


async def _wallet_old(address: str, at):
    return WalletSnapshot(
        address=address, nonce=500, first_seen_at=None, is_fresh=False
    )


def _market(
    daily_volume: Decimal | None = None, category: str = "other"
):
    async def _resolve(mid, at):
        return MarketSnapshot(
            market_id=mid,
            daily_volume=daily_volume,
            book_depth=None,
            category=category,
        )
    return _resolve


@pytest.mark.asyncio
async def test_fresh_wallet_mutation_silences_scenario1(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Mutate the fresh-wallet size threshold to $1M — hot-wallet drops.

    Without the mutation, Scenario 1's hot-wallet at $20k notional
    fires fresh_wallet. With `min_trade_size=$1M`, it must not.
    """
    capture = tmp_path / "mut.jsonl"
    import json
    with capture.open("w") as fh:
        fh.write(json.dumps(trade_event_to_record(_trade())) + "\n")

    # Drive replay with a tightened size gate via the kwarg the
    # harness exposes — no need to monkeypatch internals.
    assessments, _ = await replay_capture(
        capture,
        resolve_wallet=_wallet_fresh,
        resolve_market=_market(daily_volume=Decimal("10000000"), category="politics"),
        min_trade_size=Decimal("1000000"),   # the mutation
    )
    # With $1M threshold, no signals fire at all.
    assert assessments == []


@pytest.mark.asyncio
async def test_volume_impact_mutation_silences_scenario2(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Scale daily_volume × 50 — size_anomaly must not fire.

    Scenario 2 asserts size_anomaly on a 7% volume-impact trade.
    Scaling the market volume up by 50× brings impact to 0.14%,
    far below the 2% threshold.
    """
    import json
    capture = tmp_path / "mut.jsonl"
    with capture.open("w") as fh:
        fh.write(json.dumps(trade_event_to_record(_trade())) + "\n")

    assessments, _ = await replay_capture(
        capture,
        resolve_wallet=_wallet_old,
        resolve_market=_market(daily_volume=Decimal("1000000000"), category="politics"),
    )
    assert assessments == []


@pytest.mark.asyncio
async def test_niche_threshold_mutation_silences_scenario3(
    tmp_path,
) -> None:
    """If the niche market becomes mainstream, niche_market stops firing."""
    import json
    capture = tmp_path / "mut.jsonl"
    with capture.open("w") as fh:
        fh.write(json.dumps(trade_event_to_record(_trade())) + "\n")

    assessments, _ = await replay_capture(
        capture,
        resolve_wallet=_wallet_old,
        # 10× above the $50k niche threshold → not niche
        resolve_market=_market(daily_volume=Decimal("500000"), category="other"),
    )
    for a in assessments:
        assert "niche_market" not in a.signals_triggered


# ---------------------------------------------------------------------------
# Adversarial fixtures — exact-boundary behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nonce_5_boundary_does_not_fire_fresh_wallet(tmp_path) -> None:
    """Task 8.2.1 — nonce exactly 5 must NOT fire (documented threshold < 5)."""
    import json
    capture = tmp_path / "mut.jsonl"
    with capture.open("w") as fh:
        fh.write(json.dumps(trade_event_to_record(_trade())) + "\n")

    async def _wallet(address, at):
        return WalletSnapshot(
            address=address, nonce=5, first_seen_at=None, is_fresh=False
        )

    assessments, _ = await replay_capture(
        capture,
        resolve_wallet=_wallet,
        resolve_market=_market(daily_volume=Decimal("100000000"), category="politics"),
    )
    for a in assessments:
        assert "fresh_wallet" not in a.signals_triggered


@pytest.mark.asyncio
async def test_volume_impact_exact_threshold_boundary(tmp_path) -> None:
    """Task 8.2.2 — notional / daily_volume == 0.02 exactly must NOT fire.

    Tightens the replay heuristic: strict greater-than is the
    documented behaviour at `backtest/replay.py::replay_capture`.
    """
    import json
    capture = tmp_path / "mut.jsonl"
    # Notional = $20,000, daily_volume = $1,000,000 → impact = 2.0%
    with capture.open("w") as fh:
        fh.write(json.dumps(trade_event_to_record(_trade())) + "\n")

    assessments, _ = await replay_capture(
        capture,
        resolve_wallet=_wallet_old,
        resolve_market=_market(daily_volume=Decimal("1000000"), category="politics"),
    )
    for a in assessments:
        assert "size_anomaly" not in a.signals_triggered


# ---------------------------------------------------------------------------
# Task 8.3.1 — Isolation across scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenarios_do_not_share_captures(tmp_path) -> None:
    """Each Scenario gets its own tmp dir; no cross-pollination.

    Regression guard: if a future refactor made Scenario cache the
    capture path as a module-level constant, two consecutive
    instantiations would clobber each other.
    """
    from tests.scenarios._harness import Scenario

    a = Scenario(name="a", himalaya_binary="h", tmp_dir=tmp_path / "a")
    b = Scenario(name="b", himalaya_binary="h", tmp_dir=tmp_path / "b")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a.given_trades([_trade(wallet="0xaaa")])
    b.given_trades([_trade(wallet="0xbbb")])
    await a.when_replayed()
    await b.when_replayed()
    # Each scenario's capture file lives in its own tmp dir.
    assert list((tmp_path / "a").glob("*.jsonl")) != list((tmp_path / "b").glob("*.jsonl"))


# ---------------------------------------------------------------------------
# Task 8.2.3 — 100-wallet sybil stress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sybil_cluster_edges_match_n_choose_2(tmp_path) -> None:
    """Given N wallets from the same origin in-window, persist emits N·(N−1)/2 edges."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from polymarket_insider_tracker.profiler.funding_graph import (
        collect_shared_origins,
        persist_clusters,
    )
    from polymarket_insider_tracker.storage.models import Base
    from polymarket_insider_tracker.storage.repos import (
        FundingRepository,
        FundingTransferDTO,
    )

    url = f"sqlite+aiosqlite:///{tmp_path}/sybil.db"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    origin = "0xsybilorigin0000000000000000000000000000000"
    wallets_in_window = [f"0xsybil{i:034d}" for i in range(50)]
    wallets_outside = [f"0xsybil{50 + i:034d}" for i in range(50)]

    from datetime import timedelta
    anchor = datetime(2026, 4, 19, tzinfo=UTC)
    try:
        async with factory() as session:
            funding = FundingRepository(session)
            for i, w in enumerate(wallets_in_window):
                await funding.insert(
                    FundingTransferDTO(
                        from_address=origin,
                        to_address=w,
                        amount=Decimal("1"),
                        token="USDC",
                        tx_hash=f"0xin{i:062d}",
                        block_number=1,
                        timestamp=anchor + timedelta(minutes=i * 5),  # 50×5=250min < 48h
                    )
                )
            for i, w in enumerate(wallets_outside):
                await funding.insert(
                    FundingTransferDTO(
                        from_address=origin,
                        to_address=w,
                        amount=Decimal("1"),
                        token="USDC",
                        tx_hash=f"0xout{i:061d}",
                        block_number=1,
                        timestamp=anchor + timedelta(days=30 + i),  # out-of-window
                    )
                )
            await session.commit()

        async with factory() as session:
            clusters = await collect_shared_origins(
                session, wallets_in_window + wallets_outside
            )
            assert len(clusters) == 1
            assert clusters[0].size == 50
            edges = await persist_clusters(session, clusters)
            # N choose 2 = 50 * 49 / 2 = 1225
            assert edges == 1225
    finally:
        await engine.dispose()
