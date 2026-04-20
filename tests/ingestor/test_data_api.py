"""Tests for the Tier-2 data-api trade poller."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import httpx
import pytest

from polymarket_insider_tracker.ingestor.data_api import (
    DataAPITradePoller,
    _trade_from_api_row,
)
from polymarket_insider_tracker.ingestor.models import TradeEvent


def _canned_row(**overrides: Any) -> dict[str, Any]:
    """Default /trades row shape sufficient to exercise _trade_from_api_row."""
    row = {
        "proxyWallet": "0xdeadbeef",
        "side": "BUY",
        "asset": "12345",
        "conditionId": "0xcond",
        "size": 10.5,
        "price": 0.42,
        "timestamp": 1_700_000_000,
        "title": "Will X happen?",
        "slug": "will-x-happen",
        "eventSlug": "x-event",
        "outcome": "Yes",
        "outcomeIndex": 0,
        "name": "alice",
        "pseudonym": "Apt-Raccoon",
        "transactionHash": "0xtx1",
    }
    row.update(overrides)
    return row


def test_row_to_trade_event_maps_every_field() -> None:
    t = _trade_from_api_row(_canned_row())
    assert t.market_id == "0xcond"
    assert t.trade_id == "0xtx1"
    assert t.wallet_address == "0xdeadbeef"
    assert t.side == "BUY"
    assert t.price == Decimal("0.42")
    assert t.size == Decimal("10.5")
    assert t.asset_id == "12345"
    assert t.market_slug == "will-x-happen"
    assert t.event_title == "Will X happen?"
    assert t.outcome == "Yes"
    assert t.trader_name == "alice"
    assert t.trader_pseudonym == "Apt-Raccoon"
    assert t.timestamp.tzinfo is not None


def test_row_to_trade_event_normalizes_side() -> None:
    assert _trade_from_api_row(_canned_row(side="sell")).side == "SELL"
    assert _trade_from_api_row(_canned_row(side="buy")).side == "BUY"
    assert _trade_from_api_row(_canned_row(side="unknown")).side == "SELL"


@pytest.mark.asyncio
async def test_poller_dedupes_across_polls() -> None:
    """Two successive polls with overlapping rows emit each trade once."""
    received: list[TradeEvent] = []

    async def on_trade(t: TradeEvent) -> None:
        received.append(t)

    # httpx mock: two responses, second overlaps the first by 2 rows.
    # Rows are newest-first per the /trades contract, so later-named
    # txhashes (`0xc`) appear at the top of the second batch.
    call_count = {"n": 0}
    responses = [
        [_canned_row(transactionHash="0xb"), _canned_row(transactionHash="0xa")],
        [
            _canned_row(transactionHash="0xc"),  # new (newest)
            _canned_row(transactionHash="0xb"),  # duplicate
            _canned_row(transactionHash="0xa"),  # duplicate
        ],
    ]

    def _handler(request: httpx.Request) -> httpx.Response:
        idx = call_count["n"]
        call_count["n"] += 1
        return httpx.Response(200, json=responses[idx] if idx < len(responses) else [])

    transport = httpx.MockTransport(_handler)
    client = httpx.AsyncClient(transport=transport)
    poller = DataAPITradePoller(on_trade=on_trade, http_client=client)

    # Drive two poll cycles manually; start() would loop forever.
    await poller._poll_once()
    await poller._poll_once()

    tx_hashes = [t.trade_id for t in received]
    assert tx_hashes == ["0xa", "0xb", "0xc"]
    assert poller.stats.trades_emitted == 3
    assert poller.stats.duplicates_skipped == 2
    assert poller.stats.rows_fetched == 5

    await client.aclose()


@pytest.mark.asyncio
async def test_poller_emits_oldest_first() -> None:
    """/trades returns newest-first; consumer should see monotonic ts."""
    received: list[TradeEvent] = []

    async def on_trade(t: TradeEvent) -> None:
        received.append(t)

    rows = [
        _canned_row(transactionHash="0x3", timestamp=1_700_000_003),
        _canned_row(transactionHash="0x2", timestamp=1_700_000_002),
        _canned_row(transactionHash="0x1", timestamp=1_700_000_001),
    ]

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=rows)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    poller = DataAPITradePoller(on_trade=on_trade, http_client=client)
    await poller._poll_once()
    assert [t.trade_id for t in received] == ["0x1", "0x2", "0x3"]
    await client.aclose()


@pytest.mark.asyncio
async def test_poller_swallows_http_errors() -> None:
    """A transient 5xx doesn't crash the loop; counted in stats."""
    received: list[TradeEvent] = []

    async def on_trade(t: TradeEvent) -> None:
        received.append(t)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    poller = DataAPITradePoller(on_trade=on_trade, http_client=client)
    n = await poller._poll_once()
    assert n == 0
    assert received == []
    assert poller.stats.http_errors == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_poller_start_stop_lifecycle() -> None:
    """start() respects stop() and exits the loop."""
    received: list[TradeEvent] = []

    async def on_trade(t: TradeEvent) -> None:
        received.append(t)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_canned_row(transactionHash="0xa")])

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    poller = DataAPITradePoller(
        on_trade=on_trade,
        poll_interval=0.01,
        http_client=client,
    )
    task = asyncio.create_task(poller.start())
    await asyncio.sleep(0.05)
    await poller.stop()
    await asyncio.wait_for(task, timeout=1.0)
    assert len(received) >= 1
    assert poller.stats.polls >= 1
    await client.aclose()
