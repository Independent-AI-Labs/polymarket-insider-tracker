"""Tests for WebSocket trade stream handler."""

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from polymarket_insider_tracker.ingestor.models import TradeEvent
from polymarket_insider_tracker.ingestor.websocket import (
    ConnectionState,
    StreamStats,
    SubscriptionMode,
    TradeStreamHandler,
)


class TestStreamStats:
    """Tests for StreamStats."""

    def test_defaults(self) -> None:
        """Test default values."""
        stats = StreamStats()

        assert stats.trades_received == 0
        assert stats.reconnect_count == 0
        assert stats.last_trade_time is None
        assert stats.connected_since is None
        assert stats.last_error is None


class TestTradeStreamHandler:
    """Tests for TradeStreamHandler."""

    @pytest.fixture
    def on_trade_mock(self) -> AsyncMock:
        """Create mock trade callback."""
        return AsyncMock()

    @pytest.fixture
    def on_state_change_mock(self) -> AsyncMock:
        """Create mock state change callback."""
        return AsyncMock()

    @pytest.fixture
    def handler(
        self, on_trade_mock: AsyncMock, on_state_change_mock: AsyncMock
    ) -> TradeStreamHandler:
        """Create handler with mocks."""
        return TradeStreamHandler(
            on_trade=on_trade_mock,
            on_state_change=on_state_change_mock,
            initial_reconnect_delay=0.01,  # Fast reconnect for tests
            max_reconnect_delay=0.1,
        )

    def test_init_defaults(self, on_trade_mock: AsyncMock) -> None:
        """Test handler initialization with defaults."""
        handler = TradeStreamHandler(on_trade=on_trade_mock)

        assert handler.state == ConnectionState.DISCONNECTED
        assert handler.stats.trades_received == 0
        assert handler._host == "wss://ws-live-data.polymarket.com"

    def test_init_custom_host(self, on_trade_mock: AsyncMock) -> None:
        """Test handler with custom host."""
        handler = TradeStreamHandler(
            on_trade=on_trade_mock,
            host="wss://custom.example.com",
        )

        assert handler._host == "wss://custom.example.com"

    def test_init_with_event_filter(self, on_trade_mock: AsyncMock) -> None:
        """Test handler with event filter."""
        handler = TradeStreamHandler(
            on_trade=on_trade_mock,
            event_filter="presidential-election-2024",
        )

        assert handler._event_filter == "presidential-election-2024"

    def test_build_subscription_message_no_filter(self, handler: TradeStreamHandler) -> None:
        """Test building subscription message without filters."""
        msg = handler._build_subscription_message()

        assert msg == {"subscriptions": [{"topic": "activity", "type": "trades"}]}

    def test_build_subscription_message_with_event_filter(self, on_trade_mock: AsyncMock) -> None:
        """Test building subscription message with event filter."""
        handler = TradeStreamHandler(
            on_trade=on_trade_mock,
            event_filter="test-event",
        )
        msg = handler._build_subscription_message()

        assert msg["subscriptions"][0]["filters"] == json.dumps({"event_slug": "test-event"})

    def test_build_subscription_message_with_market_filter(self, on_trade_mock: AsyncMock) -> None:
        """Test building subscription message with market filter."""
        handler = TradeStreamHandler(
            on_trade=on_trade_mock,
            market_filter="test-market",
        )
        msg = handler._build_subscription_message()

        assert msg["subscriptions"][0]["filters"] == json.dumps({"market_slug": "test-market"})

    @pytest.mark.asyncio
    async def test_handle_message_trade(
        self, handler: TradeStreamHandler, on_trade_mock: AsyncMock
    ) -> None:
        """Test handling a valid trade message."""
        message = json.dumps(
            {
                "topic": "activity",
                "type": "trades",
                "payload": {
                    "conditionId": "0xmarket",
                    "transactionHash": "0xtx",
                    "proxyWallet": "0xwallet",
                    "side": "BUY",
                    "outcome": "Yes",
                    "price": 0.65,
                    "size": 100,
                    "timestamp": 1704067200,
                    "asset": "token123",
                },
            }
        )

        await handler._handle_message(message)

        on_trade_mock.assert_called_once()
        trade: TradeEvent = on_trade_mock.call_args[0][0]
        assert trade.market_id == "0xmarket"
        assert trade.side == "BUY"
        assert trade.price == Decimal("0.65")
        assert handler.stats.trades_received == 1

    @pytest.mark.asyncio
    async def test_handle_message_non_trade(
        self, handler: TradeStreamHandler, on_trade_mock: AsyncMock
    ) -> None:
        """Test handling a non-trade message."""
        message = json.dumps(
            {
                "topic": "comments",
                "type": "comment_created",
                "payload": {"body": "Hello"},
            }
        )

        await handler._handle_message(message)

        on_trade_mock.assert_not_called()
        assert handler.stats.trades_received == 0

    @pytest.mark.asyncio
    async def test_handle_message_invalid_json(
        self, handler: TradeStreamHandler, on_trade_mock: AsyncMock
    ) -> None:
        """Test handling invalid JSON message."""
        await handler._handle_message("not valid json")

        on_trade_mock.assert_not_called()
        assert handler.stats.trades_received == 0

    @pytest.mark.asyncio
    async def test_handle_message_callback_error(
        self, handler: TradeStreamHandler, on_trade_mock: AsyncMock
    ) -> None:
        """Test that callback errors don't crash the handler."""
        on_trade_mock.side_effect = ValueError("Callback error")

        message = json.dumps(
            {
                "topic": "activity",
                "type": "trades",
                "payload": {
                    "conditionId": "0x",
                    "transactionHash": "0x",
                    "proxyWallet": "0x",
                    "side": "BUY",
                    "price": 0.5,
                    "size": 10,
                },
            }
        )

        # Should not raise
        await handler._handle_message(message)

        # Trade was still counted
        assert handler.stats.trades_received == 1

    @pytest.mark.asyncio
    async def test_set_state_calls_callback(
        self, handler: TradeStreamHandler, on_state_change_mock: AsyncMock
    ) -> None:
        """Test that state changes trigger callback."""
        await handler._set_state(ConnectionState.CONNECTING)

        on_state_change_mock.assert_called_once_with(ConnectionState.CONNECTING)
        assert handler.state == ConnectionState.CONNECTING

    @pytest.mark.asyncio
    async def test_set_state_same_state_no_callback(
        self, handler: TradeStreamHandler, on_state_change_mock: AsyncMock
    ) -> None:
        """Test that same state doesn't trigger callback."""
        handler._state = ConnectionState.CONNECTED

        await handler._set_state(ConnectionState.CONNECTED)

        on_state_change_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, handler: TradeStreamHandler) -> None:
        """Test stop when handler is not running."""
        # Should not raise
        await handler.stop()

    @pytest.mark.asyncio
    async def test_connect_sends_subscription(
        self,
        handler: TradeStreamHandler,
        on_state_change_mock: AsyncMock,  # noqa: ARG002
    ) -> None:
        """Test that connection sends subscription message."""
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()

        with patch(
            "polymarket_insider_tracker.ingestor.websocket.ws_connect",
            AsyncMock(return_value=mock_ws),
        ):
            ws = await handler._connect()

            assert ws is mock_ws
            mock_ws.send.assert_called_once()

            # Verify subscription message
            sent_msg = json.loads(mock_ws.send.call_args[0][0])
            assert "subscriptions" in sent_msg
            assert sent_msg["subscriptions"][0]["topic"] == "activity"
            assert sent_msg["subscriptions"][0]["type"] == "trades"

    @pytest.mark.asyncio
    async def test_cleanup_closes_websocket(self, handler: TradeStreamHandler) -> None:
        """Test that cleanup closes the WebSocket."""
        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock()
        handler._ws = mock_ws

        await handler._cleanup()

        mock_ws.close.assert_called_once()
        assert handler._ws is None
        assert handler.state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_context_manager(self, on_trade_mock: AsyncMock) -> None:
        """Test async context manager."""
        handler = TradeStreamHandler(on_trade=on_trade_mock)

        async with handler:
            pass

        # Should be stopped after exiting context
        assert handler._running is False


class TestTradeStreamHandlerIntegration:
    """Integration tests for TradeStreamHandler.

    These tests verify the full message flow with mocked WebSocket.
    """

    @pytest.mark.asyncio
    async def test_start_and_receive_trades(self) -> None:
        """Test starting handler and receiving trades."""
        received_trades: list[TradeEvent] = []

        async def on_trade(trade: TradeEvent) -> None:
            received_trades.append(trade)

        handler = TradeStreamHandler(
            on_trade=on_trade,
            initial_reconnect_delay=0.01,
        )

        trade_message = json.dumps(
            {
                "topic": "activity",
                "type": "trades",
                "payload": {
                    "conditionId": "0xtest",
                    "transactionHash": "0xtx",
                    "proxyWallet": "0xwallet",
                    "side": "BUY",
                    "outcome": "Yes",
                    "price": 0.75,
                    "size": 50,
                    "timestamp": 1704067200,
                    "asset": "token",
                },
            }
        )

        # Create a proper async iterable mock WebSocket
        class MockWebSocket:
            """Mock WebSocket that yields one message then stops."""

            def __init__(self, handler: TradeStreamHandler, message: str):
                self.handler = handler
                self.message = message
                self.sent = False

            async def send(self, _msg: str) -> None:
                pass

            async def close(self) -> None:
                pass

            def __aiter__(self):
                return self

            async def __anext__(self) -> str:
                if not self.sent:
                    self.sent = True
                    return self.message
                # Stop the handler and raise StopAsyncIteration
                await self.handler.stop()
                raise StopAsyncIteration

        mock_ws = MockWebSocket(handler, trade_message)

        with patch(
            "polymarket_insider_tracker.ingestor.websocket.ws_connect",
            AsyncMock(return_value=mock_ws),
        ):
            # Run with timeout to prevent hanging
            try:
                await asyncio.wait_for(handler.start(), timeout=1.0)
            except TimeoutError:
                await handler.stop()

        # Verify trade was received
        assert len(received_trades) == 1
        assert received_trades[0].market_id == "0xtest"
        assert received_trades[0].side == "BUY"
        assert received_trades[0].price == Decimal("0.75")


class TestCLOBSubscriptionMode:
    """Tests for the CLOB market-channel subscription variant."""

    def test_mode_autodetects_from_host_path(self) -> None:
        """Host containing /ws/market switches the mode to CLOB_MARKET."""
        handler = TradeStreamHandler(
            on_trade=AsyncMock(),
            host="wss://ws-subscriptions-clob.polymarket.com/ws/market",
            asset_ids=["tok-a"],
        )
        assert handler._mode is SubscriptionMode.CLOB_MARKET

    def test_mode_defaults_to_activity_for_root_host(self) -> None:
        """A bare host with no `/ws/market` stays on the activity protocol."""
        handler = TradeStreamHandler(
            on_trade=AsyncMock(),
            host="wss://ws-live-data.polymarket.com",
        )
        assert handler._mode is SubscriptionMode.ACTIVITY

    def test_build_subscription_message_clob_mode(self) -> None:
        """CLOB subscribe is a flat `{assets_ids, type}` object, not wrapped."""
        handler = TradeStreamHandler(
            on_trade=AsyncMock(),
            host="wss://ws-subscriptions-clob.polymarket.com/ws/market",
            asset_ids=["tok-a", "tok-b"],
        )
        msg = handler._build_subscription_message()
        assert msg == {"assets_ids": ["tok-a", "tok-b"], "type": "market"}
        # No `subscriptions` wrapper — CLOB differs from the activity feed.
        assert "subscriptions" not in msg

    @pytest.mark.asyncio
    async def test_handle_clob_last_trade_price_event(self) -> None:
        """A last_trade_price frame becomes a TradeEvent with correct fields."""
        received: list[TradeEvent] = []

        async def on_trade(event: TradeEvent) -> None:
            received.append(event)

        handler = TradeStreamHandler(
            on_trade=on_trade,
            host="wss://ws-subscriptions-clob.polymarket.com/ws/market",
            asset_ids=["tok-a"],
            asset_id_to_condition={
                "tok-a": {
                    "condition_id": "0xcond",
                    "market_slug": "will-x-happen",
                    "event_slug": "x-event",
                    "event_title": "Will X happen?",
                    "outcome": "Yes",
                    "outcome_index": "0",
                }
            },
        )

        frame = json.dumps(
            {
                "event_type": "last_trade_price",
                "asset_id": "tok-a",
                "market": "0xcond",
                "price": "0.52",
                "side": "SELL",
                "size": "120",
                "timestamp": "1712345678901",  # ms
                "fee_rate_bps": "0",
            }
        )
        await handler._handle_message(frame)

        assert len(received) == 1
        trade = received[0]
        assert trade.market_id == "0xcond"
        assert trade.asset_id == "tok-a"
        assert trade.side == "SELL"
        assert trade.price == Decimal("0.52")
        assert trade.size == Decimal("120")
        assert trade.market_slug == "will-x-happen"
        assert trade.event_title == "Will X happen?"
        assert trade.outcome == "Yes"
        # Timestamp parsed from ms, tz-aware, within 1s of 2024-04-05T17:34:38Z.
        assert trade.timestamp.tzinfo is not None
        assert trade.timestamp.year == 2024
        assert handler.stats.trades_received == 1

    @pytest.mark.asyncio
    async def test_handle_clob_book_frame_ignored(self) -> None:
        """book / price_change frames don't fire the trade callback."""
        received: list[TradeEvent] = []

        async def on_trade(event: TradeEvent) -> None:
            received.append(event)

        handler = TradeStreamHandler(
            on_trade=on_trade,
            host="wss://ws-subscriptions-clob.polymarket.com/ws/market",
            asset_ids=["tok-a"],
        )
        book_frame = json.dumps(
            {
                "event_type": "book",
                "asset_id": "tok-a",
                "bids": [],
                "asks": [],
            }
        )
        await handler._handle_message(book_frame)
        assert received == []
        assert handler.stats.trades_received == 0

    @pytest.mark.asyncio
    async def test_handle_clob_array_batched_frames(self) -> None:
        """Polymarket batches initial CLOB frames as a JSON array — unpack it."""
        received: list[TradeEvent] = []

        async def on_trade(event: TradeEvent) -> None:
            received.append(event)

        handler = TradeStreamHandler(
            on_trade=on_trade,
            host="wss://ws-subscriptions-clob.polymarket.com/ws/market",
            asset_ids=["tok-a"],
            asset_id_to_condition={"tok-a": {"condition_id": "0xc"}},
        )
        batch = json.dumps(
            [
                {"event_type": "book", "asset_id": "tok-a"},
                {
                    "event_type": "last_trade_price",
                    "asset_id": "tok-a",
                    "market": "0xc",
                    "price": "0.31",
                    "side": "BUY",
                    "size": "5",
                    "timestamp": "1712345678901",
                },
            ]
        )
        await handler._handle_message(batch)
        assert len(received) == 1
        assert received[0].price == Decimal("0.31")
