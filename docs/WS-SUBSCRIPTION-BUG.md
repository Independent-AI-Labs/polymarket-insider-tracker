# Polymarket WebSocket — CLOB subscription protocol

**Status:** resolved for the CLOB market channel; follow-up open on
wallet attribution.
**Discovered:** 2026-04-19 live capture attempt.
**Resolved:** 2026-04-19 same-day — `TradeStreamHandler` now speaks
the CLOB `/ws/market` protocol; a 90-second capture landed 130
trades across 28 markets.

## What was wrong

`src/polymarket_insider_tracker/ingestor/websocket.py` originally
sent the legacy activity-feed subscribe for every host:

```python
{"subscriptions": [{"topic": "activity", "type": "trades"}]}
```

That payload is correct for `wss://ws-live-data.polymarket.com`, but
this environment has that hostname pinned to 127.0.0.1 at the
resolver layer (confirmed via `resolvectl query`). The only
Polymarket websocket reachable here is
`wss://ws-subscriptions-clob.polymarket.com/ws/market` (pinned in
`/etc/hosts` to `104.18.34.205`), and that endpoint wants a
different shape:

```python
{"assets_ids": ["<token_id>", ...], "type": "market"}
```

— a **flat** object, no `subscriptions` wrapper, and the server
only streams frames for asset IDs the client explicitly lists.

## Fix

1. `TradeStreamHandler` gained a `SubscriptionMode` enum
   (`ACTIVITY | CLOB_MARKET`). The mode auto-detects from the host
   (`/ws/market` path → CLOB_MARKET) and can be overridden.
2. `_build_subscription_message` emits the right shape per mode.
3. `_handle_message` parses both `topic:activity/trades` frames and
   CLOB `event_type:last_trade_price` frames into `TradeEvent`s.
   Array-batched frames (the initial `book` snapshot ships as a list
   of per-asset dicts) are unpacked.
4. `scripts/direct-capture.py` now fetches the top-N active markets
   from `gamma-api.polymarket.com`, extracts `clobTokenIds`, and
   passes `asset_ids` + a metadata lookup table into the handler so
   `last_trade_price` frames (which only carry `asset_id` + `market`)
   can still produce fully-populated `TradeEvent`s.

Tests: `tests/ingestor/test_websocket.py::TestCLOBSubscriptionMode`
exercises mode autodetect, both subscribe shapes, the `last_trade_price`
→ `TradeEvent` path, book-frame suppression, and array-batched
unpacking.

Live smoke — top 50 markets by 24h volume, 90s:

```
captured 130 events to data/captures/clob-smoke.jsonl
unique markets: 28
```

## Open follow-up — wallet attribution

The CLOB `/ws/market` channel is anonymized. `last_trade_price`
frames expose `asset_id`, `market` (= conditionId), `side`, `price`,
`size`, and `timestamp` — **not** the proxy wallet. The legacy
activity feed did expose `proxyWallet`, which the insider-tracking
detectors rely on (`FreshWalletDetector`, `SizeAnomalyDetector`,
`FundingTracer` clustering).

Options, in priority order:

1. **Polygon-chain correlation (preferred).** Each CLOB trade hits
   the Polygon CTF exchange on-chain within a block or two. A tight
   loop in `ChainIndexer` can match (market_id, size, price,
   timestamp±30s) against the on-chain log stream to recover the
   proxy wallet. This turns the CLOB feed into a partial-information
   signal that the chain-indexer closes the loop on.
2. **Authenticated `/ws/user` channel.** Only streams the
   authenticated user's own trades. Not useful for a public
   insider-tracker surface, only for dogfooding.
3. **Bypass the resolver hijack of `ws-live-data.polymarket.com`.**
   Requires operator-level access to the DNS layer, which is outside
   the scope of a code fix.

Option 1 is the right path. Tracked as a new task in
`IMPLEMENTATION-TODOS.md`.

## Related tasks

`docs/IMPLEMENTATION-TODOS.md` Phase 9.1.1 (72-hour capture) is now
unblocked modulo the wallet-attribution follow-up — the capture
itself runs and persists trades; pairing them back to wallets
happens inside the pipeline's chain indexer, not inside the
WebSocket layer.
