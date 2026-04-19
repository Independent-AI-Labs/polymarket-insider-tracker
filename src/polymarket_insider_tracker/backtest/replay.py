"""Replay a captured WebSocket trade stream through the detector stack.

Design note — we do NOT drive the full `Pipeline` here. The Pipeline's
wallet analyzer and funding tracer depend on live Polygon RPC state,
which today reflects "now" not "the moment of the captured trade". So
running the full stack against an old capture would score detectors
against the wrong inputs.

Instead, the replayer expects the caller to supply resolver protocols
for the two external-state lookups (wallet freshness, market depth /
volume). For a hermetic replay, the caller can snapshot these at
capture time and provide deterministic fakes; for a "what would the
stack say today?" replay, the caller plugs in the live WalletAnalyzer
and MetadataSync.

Capture format (one JSON object per line):

```
{"trade_id":"...", "market_id":"...", "wallet_address":"...", "side":"BUY",
 "outcome":"Yes", "outcome_index":0, "price":"0.12", "size":"1000",
 "timestamp":"2026-04-19T13:00:00+00:00", "asset_id":"...",
 "market_slug":"...", "event_slug":"...", "event_title":"...",
 "trader_name":"", "trader_pseudonym":""}
```

Matches the serialisation `EventPublisher._serialize_trade_event` emits
to the Redis `trades` stream, so capture-trades.py can write the same
shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Literal, Protocol

from polymarket_insider_tracker.ingestor.models import TradeEvent


@dataclass(frozen=True)
class WalletSnapshot:
    """Resolver output for a wallet at a specific capture timestamp."""

    address: str
    nonce: int
    first_seen_at: datetime | None
    is_fresh: bool


@dataclass(frozen=True)
class MarketSnapshot:
    """Resolver output for a market at a specific capture timestamp."""

    market_id: str
    daily_volume: Decimal | None
    book_depth: Decimal | None
    category: str | None = None


class WalletProfileResolver(Protocol):
    """Callable returning a WalletSnapshot for (address, at_timestamp)."""

    async def __call__(self, address: str, at: datetime) -> WalletSnapshot | None: ...


class MarketMetadataResolver(Protocol):
    """Callable returning a MarketSnapshot for (market_id, at_timestamp)."""

    async def __call__(self, market_id: str, at: datetime) -> MarketSnapshot | None: ...


@dataclass(frozen=True)
class ReplayAssessment:
    """A detector assessment produced during replay.

    This is a pared-down, serialisable cousin of the production
    `RiskAssessment` — it carries only the fields the outcome
    classifier and metrics aggregator need. We don't reuse
    RiskAssessment directly because it pulls in the full detector
    model stack.
    """

    assessment_id: str
    trade: TradeEvent
    signals_triggered: tuple[str, ...]
    weighted_score: float
    wallet_snapshot: WalletSnapshot | None = None
    market_snapshot: MarketSnapshot | None = None


def iter_capture(path: Path | str) -> Iterable[TradeEvent]:
    """Yield TradeEvents from a jsonl capture file.

    Missing optional fields get their dataclass defaults. Malformed
    lines raise `ValueError`; it's better to fail loudly than to
    silently skip rows (which would distort the metrics).
    """
    capture_path = Path(path)
    with capture_path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                msg = f"{capture_path}:{lineno}: invalid JSON: {exc}"
                raise ValueError(msg) from exc
            yield _trade_event_from_record(record)


def _trade_event_from_record(record: dict[str, Any]) -> TradeEvent:
    """Rehydrate a TradeEvent from a capture record.

    Mirrors `ingestor.publisher._deserialize_trade_event` but operates
    on plain dicts (not Redis bytes), so it works off the jsonl file
    that `capture-trades.py` writes.
    """
    # Parse timestamp — accept ISO-8601 or Unix seconds
    raw_ts = record.get("timestamp")
    if isinstance(raw_ts, (int, float)):
        timestamp = datetime.fromtimestamp(float(raw_ts), tz=UTC)
    else:
        try:
            timestamp = datetime.fromisoformat(str(raw_ts))
        except (TypeError, ValueError):
            timestamp = datetime.now(UTC)

    side_raw = str(record.get("side", "BUY")).upper()
    side: Literal["BUY", "SELL"] = "BUY" if side_raw == "BUY" else "SELL"

    return TradeEvent(
        market_id=str(record.get("market_id", "")),
        trade_id=str(record.get("trade_id", "")),
        wallet_address=str(record.get("wallet_address", "")),
        side=side,
        outcome=str(record.get("outcome", "")),
        outcome_index=int(record.get("outcome_index", 0) or 0),
        price=Decimal(str(record.get("price", "0"))),
        size=Decimal(str(record.get("size", "0"))),
        timestamp=timestamp,
        asset_id=str(record.get("asset_id", "")),
        market_slug=str(record.get("market_slug", "")),
        event_slug=str(record.get("event_slug", "")),
        event_title=str(record.get("event_title", "")),
        trader_name=str(record.get("trader_name", "")),
        trader_pseudonym=str(record.get("trader_pseudonym", "")),
    )


def trade_event_to_record(event: TradeEvent) -> dict[str, str]:
    """Serialise a TradeEvent back into the jsonl capture shape.

    Used by `capture-trades.py` so replay and capture stay in sync;
    exposed here to avoid importing the Redis-flavoured helper in
    `publisher.py` (which would pull in aioredis on systems that don't
    have it installed just to write a capture file).
    """
    return {
        "market_id": event.market_id,
        "trade_id": event.trade_id,
        "wallet_address": event.wallet_address,
        "side": event.side,
        "outcome": event.outcome,
        "outcome_index": str(event.outcome_index),
        "price": str(event.price),
        "size": str(event.size),
        "timestamp": event.timestamp.isoformat(),
        "asset_id": event.asset_id,
        "market_slug": event.market_slug,
        "event_slug": event.event_slug,
        "event_title": event.event_title,
        "trader_name": event.trader_name,
        "trader_pseudonym": event.trader_pseudonym,
    }


@dataclass
class ReplayStats:
    """Running totals for operator visibility."""

    trades_processed: int = 0
    assessments_emitted: int = 0
    wallet_resolver_failures: int = 0
    market_resolver_failures: int = 0
    errors: list[str] = field(default_factory=list)


async def replay_capture(
    capture_path: Path | str,
    *,
    resolve_wallet: WalletProfileResolver,
    resolve_market: MarketMetadataResolver,
    min_trade_size: Decimal = Decimal("1000"),
) -> tuple[list[ReplayAssessment], ReplayStats]:
    """Replay a capture and emit one ReplayAssessment per scored trade.

    A scored trade is one where at least one of the detector heuristics
    would fire. For pure-Python backtesting we inline the heuristics
    here (rather than calling the async detectors that need Redis) —
    the plan is that the live script variant swaps this out for the
    real detector instances.

    Heuristics applied here (matching production defaults):
    - fresh_wallet: nonce < 5 AND notional >= min_trade_size.
    - size_anomaly: notional / daily_volume > 0.02 (2% volume impact).
    - niche_market: daily_volume < 50_000 AND notional >= 1000.

    This mirrors the production defaults documented in `docs/CLAIMS-AUDIT.md`.
    """
    stats = ReplayStats()
    assessments: list[ReplayAssessment] = []

    for event in iter_capture(capture_path):
        stats.trades_processed += 1
        notional = event.price * event.size

        wallet = await resolve_wallet(event.wallet_address, event.timestamp)
        if wallet is None:
            stats.wallet_resolver_failures += 1
        market = await resolve_market(event.market_id, event.timestamp)
        if market is None:
            stats.market_resolver_failures += 1

        signals: list[str] = []
        if (
            wallet is not None
            and wallet.is_fresh
            and notional >= min_trade_size
        ):
            signals.append("fresh_wallet")
        if (
            market is not None
            and market.daily_volume is not None
            and market.daily_volume > 0
            and notional / market.daily_volume > Decimal("0.02")
        ):
            signals.append("size_anomaly")
        if (
            market is not None
            and market.daily_volume is not None
            and market.daily_volume < Decimal("50000")
            and notional >= Decimal("1000")
        ):
            signals.append("niche_market")

        if not signals:
            continue

        # Mimic the scorer's weighted score with the production weights.
        weights = {"fresh_wallet": 0.40, "size_anomaly": 0.35, "niche_market": 0.25}
        score = sum(weights.get(s, 0.0) for s in signals)
        if len(signals) >= 2:
            score *= 1.2
        if len(signals) >= 3:
            score *= 1.3
        score = min(score, 1.0)

        assessments.append(
            ReplayAssessment(
                assessment_id=f"replay-{event.trade_id}",
                trade=event,
                signals_triggered=tuple(signals),
                weighted_score=score,
                wallet_snapshot=wallet,
                market_snapshot=market,
            )
        )
        stats.assessments_emitted += 1

    return assessments, stats
