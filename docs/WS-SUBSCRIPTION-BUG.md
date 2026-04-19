# Polymarket WebSocket — zero-trade subscription bug

**Status:** open
**Discovered:** 2026-04-19 live capture attempt
**Repro:**

```bash
POLYMARKET_API_KEY=... POLYMARKET_API_SECRET=... POLYMARKET_API_PASSPHRASE=... \
  uv run python scripts/direct-capture.py --duration 120
# Log: "Connected … and subscribed to trades"
# But file stays at 0 lines.
```

## What's happening

`src/polymarket_insider_tracker/ingestor/websocket.py:140-153`
sends the subscription payload:

```python
{
    "subscriptions": [
        {"topic": "activity", "type": "trades"}
    ]
}
```

The TCP + TLS handshake completes, the WebSocket server accepts
the subscribe, but no `topic: "activity"` messages ever arrive.
Either:

1. The CLOB endpoint (`wss://ws-subscriptions-clob.polymarket.com/ws/market`)
   no longer honours the `topic: activity` protocol this code
   targets — Polymarket may have split market-data and activity
   into separate endpoints.
2. The subscribe payload needs an `assets_ids` or `markets` filter
   for the server to start streaming.
3. The user channel (`ws/user`) is where trade executions live
   now; `ws/market` is book-update-only.

## What the CI + scenario tests already cover

- `tests/ingestor/test_websocket.py`: 18 tests against a mocked
  in-process WS server. All pass, confirming the client handles
  the legacy protocol shape correctly.
- `tests/backtest/test_backtest_cli.py`: end-to-end replay CLI
  against a synthetic 3-trade capture lands
  `detector_metrics` rows in SQLite. Passes today.

So the scaffolding works; the only gap is the *live* subscription
format.

## Next step

- Open an issue tagged `ingestor`. Assignee needs to:
  1. Reproduce with `websocat` or a small test harness to confirm
     which URL + payload produces trade frames.
  2. Update `TradeStreamHandler._build_subscription_message` (and
     `DEFAULT_WS_HOST` if the endpoint moved).
  3. Add an integration test using the real endpoint in a
     feature-gated CI job (skipped by default, opt-in via
     `POLYMARKET_LIVE_TESTS=1`).

## Related tasks

- `docs/IMPLEMENTATION-TODOS.md` Phase 9.1.1-9.3.2: these stay
  open-with-reason. Once the subscription protocol is fixed, the
  72-hour capture can proceed and the sanity-band CI check (9.2.4)
  will have real data to score against.
