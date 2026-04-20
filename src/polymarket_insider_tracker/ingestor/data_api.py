"""Tier-2 trade source — polls `data-api.polymarket.com/trades`.

The CLOB WebSocket (Tier 1) ships anonymized frames; the `/trades`
REST endpoint returns the same trades with `proxyWallet` attached.
For capture-to-jsonl + backtest replay that's the whole point, so
this module is the default source behind `scripts/direct-capture.py`
until a Tier-3 on-chain indexer lands.

**Endpoint reality (measured, not documented):**

- `limit` caps at 1000 (values above are silently clamped). 1000
  trades cover ~40 s of feed at current Polymarket volume
  (~25 trades/sec).
- Responses are served from a Cloudflare edge cache; successive
  hits from the same node for the same URL return identical rows
  with an `age` header counting up. Cache TTL is in minutes.
  `Cache-Control: no-cache` and query cache-busters don't help —
  the origin itself serves a window that only advances periodically.
- Practical consequence: polling faster than ~30 s is wasted
  bandwidth; polling slower than the window coverage (~40 s) risks
  gaps in bursty traffic.

Defaults: 30 s poll × 1000 limit. Overlap ~10 s → dedupe by
`transactionHash`. Ring-buffered dedupe set keeps memory bounded.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import httpx

from polymarket_insider_tracker.ingestor.models import TradeEvent

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://data-api.polymarket.com"
DEFAULT_POLL_INTERVAL = 30.0
DEFAULT_LIMIT = 1000
# Size of the dedupe window. 10k trades × 5-8 s gap per batch is
# ~15-20 min of coverage, well beyond any reasonable poll interval.
DEFAULT_DEDUPE_WINDOW = 10_000


TradeCallback = Callable[[TradeEvent], Awaitable[None]]


@dataclass
class PollerStats:
    """Operational counters exposed for health / telemetry."""

    polls: int = 0
    rows_fetched: int = 0
    trades_emitted: int = 0
    duplicates_skipped: int = 0
    http_errors: int = 0
    last_poll_at: datetime | None = None


def _trade_from_api_row(row: dict[str, Any]) -> TradeEvent:
    """Map one `/trades` response row into a `TradeEvent`.

    The public `/trades` schema matches the legacy activity-feed WS
    schema closely enough that we reuse the `TradeEvent` layout; only
    a handful of field names differ (`proxyWallet`, `asset`,
    `conditionId` all come through 1:1).
    """
    ts_raw = row.get("timestamp", 0)
    try:
        timestamp = datetime.fromtimestamp(int(ts_raw), tz=UTC)
    except (TypeError, ValueError):
        timestamp = datetime.now(UTC)

    side_raw = str(row.get("side", "BUY")).upper()
    side: Literal["BUY", "SELL"] = "BUY" if side_raw == "BUY" else "SELL"

    return TradeEvent(
        market_id=str(row.get("conditionId", "")),
        trade_id=str(row.get("transactionHash", "")),
        wallet_address=str(row.get("proxyWallet", "")),
        side=side,
        outcome=str(row.get("outcome", "")),
        outcome_index=int(row.get("outcomeIndex", 0) or 0),
        price=Decimal(str(row.get("price", 0))),
        size=Decimal(str(row.get("size", 0))),
        timestamp=timestamp,
        asset_id=str(row.get("asset", "")),
        market_slug=str(row.get("slug", "")),
        event_slug=str(row.get("eventSlug", "")),
        event_title=str(row.get("title", "")),
        trader_name=str(row.get("name", "")),
        trader_pseudonym=str(row.get("pseudonym", "")),
    )


class DataAPITradePoller:
    """Async poll loop against `data-api.polymarket.com/trades`.

    Usage mirrors `TradeStreamHandler`:

        >>> async def on_trade(t: TradeEvent): ...
        >>> poller = DataAPITradePoller(on_trade=on_trade)
        >>> await poller.start()   # blocks until stop()
    """

    def __init__(
        self,
        on_trade: TradeCallback,
        *,
        base_url: str = DEFAULT_BASE_URL,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        limit: int = DEFAULT_LIMIT,
        dedupe_window: int = DEFAULT_DEDUPE_WINDOW,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._on_trade = on_trade
        self._base_url = base_url.rstrip("/")
        self._poll_interval = poll_interval
        self._limit = limit
        self._seen: deque[str] = deque(maxlen=dedupe_window)
        self._seen_set: set[str] = set()
        self._client = http_client
        self._owns_client = http_client is None
        self._running = False
        self._stats = PollerStats()

    @property
    def stats(self) -> PollerStats:
        return self._stats

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    def _remember(self, tx_hash: str) -> bool:
        """Return True if `tx_hash` is new; False if a duplicate."""
        if not tx_hash:
            # Rows without a tx hash can't be deduped safely — emit
            # every time. Shouldn't happen against the real API but
            # defensive in case of a schema surprise.
            return True
        if tx_hash in self._seen_set:
            return False
        if len(self._seen) == self._seen.maxlen and self._seen:
            # Evict the oldest to keep _seen_set bounded.
            evicted = self._seen[0]
            self._seen_set.discard(evicted)
        self._seen.append(tx_hash)
        self._seen_set.add(tx_hash)
        return True

    async def _poll_once(self) -> int:
        """Fetch one batch; return the count of newly-emitted trades."""
        client = await self._ensure_client()
        try:
            resp = await client.get(
                f"{self._base_url}/trades",
                params={"limit": self._limit},
            )
            resp.raise_for_status()
            rows: list[dict[str, Any]] = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            self._stats.http_errors += 1
            logger.warning("data-api poll failed: %s", exc)
            return 0

        self._stats.polls += 1
        self._stats.rows_fetched += len(rows)
        self._stats.last_poll_at = datetime.now(UTC)

        emitted = 0
        # /trades returns newest-first; iterate oldest-first so
        # downstream consumers see monotonic timestamps.
        for row in reversed(rows):
            tx_hash = str(row.get("transactionHash", ""))
            if not self._remember(tx_hash):
                self._stats.duplicates_skipped += 1
                continue
            try:
                trade = _trade_from_api_row(row)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to parse row %s: %s", tx_hash, exc)
                continue
            try:
                await self._on_trade(trade)
            except Exception as exc:  # noqa: BLE001
                logger.error("trade callback raised: %s", exc)
            emitted += 1
        self._stats.trades_emitted += emitted
        return emitted

    async def start(self) -> None:
        """Run the poll loop until `stop()` is called."""
        if self._running:
            logger.warning("poller already running")
            return
        self._running = True
        try:
            while self._running:
                emitted = await self._poll_once()
                logger.debug(
                    "poll: +%d trades (total emitted=%d, skipped=%d, errors=%d)",
                    emitted,
                    self._stats.trades_emitted,
                    self._stats.duplicates_skipped,
                    self._stats.http_errors,
                )
                if not self._running:
                    break
                try:
                    await asyncio.sleep(self._poll_interval)
                except asyncio.CancelledError:
                    break
        finally:
            await self._cleanup()

    async def stop(self) -> None:
        self._running = False

    async def _cleanup(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "DataAPITradePoller":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()
