# Section 1 — "New wallets, big bets" (Fresh Wallets)

## Thesis

Wallets created within the last 48 hours, with fewer than five
lifetime Polygon transactions, that nonetheless submit
≥ $1,000 trades in the first minutes after market creation or
hours before the underlying event. The operating hypothesis — well
documented in the public Polymarket incidents below — is that
insiders routinely spin up new wallets to compartmentalise exposure
rather than trade from a persistent identity.

## Validated cases

### Case 1.1 — Venezuela / Maduro operation bet (Jan 2026)

- **What happened**: a newly created Polymarket wallet placed
  "over $30,000" on a market asking whether Nicolás Maduro would
  be removed from office by end of January 2026. A few hours later,
  the Trump administration conducted a raid/capture operation
  against Maduro; the bet paid out roughly $400,000 (~13× return).
- **Wallet signature**: new account, minimal prior participation,
  one oversized single bet, precise outcome match.
- **Why this is a fresh-wallet case, not just a whale case**: the
  wallet's age + thin trade history is exactly what
  `FreshWalletDetector` is parameterised on
  (`DEFAULT_MAX_NONCE = 5`, `DEFAULT_MAX_AGE_HOURS = 48`,
  `src/polymarket_insider_tracker/detector/fresh_wallet.py:18-20`).
- **Congressional attention**: Rep. Ritchie Torres introduced
  legislation explicitly citing this Polymarket trade to extend
  insider-trading prohibitions to federal prediction markets.
- **Sources**: [ritchietorres.house.gov press release](https://ritchietorres.house.gov/posts/in-response-to-suspicious-polymarket-trade-preceding-maduro-operation-rep-ritchie-torres-introduces-legislation-to-crack-down-on-insider-trading-on-prediction-markets), [CBS News coverage](https://www.cbsnews.com/news/polymarket-insider-trading-rules-iran-war-venezuela/).

### Case 1.2 — Axiom pre-reveal (2025-2026)

- **What happened**: Lookonchain identified 12 newly created wallets
  that bet heavily on Axiom-related Polymarket markets just before
  a major project reveal. Combined net profit: >$1M.
- **Pattern**: the cluster was detectable because each wallet had
  a short on-chain history and funded into identical markets within
  the same few-hour window.
- **Sources**: [yellow.com summary of the Polymarket investigation](https://yellow.com/news/polymarket-insider-copy-trading-investigation).

### Case 1.3 — Polymarket's own insider-tracking market got insider-traded (Feb 2026)

- **What happened**: Polymarket ran a meta market on whether an
  insider-trading investigation would be opened, and a fresh wallet
  appeared to insider-trade *that* market too. CoinDesk covered the
  irony.
- **Relevance**: demonstrates the fresh-wallet pattern is live and
  reproducible independent of any single news event.
- **Sources**: [CoinDesk, "Polymarket bettors appear to have insider-traded a market designed to catch insider traders"](https://www.coindesk.com/markets/2026/02/27/polymarket-bettors-appear-to-have-insider-traded-on-a-market-designed-to-catch-insider-traders).

### Market-wide framing

Commercial tools like PolymarketScan, Polywhaler, PolyTrack tag
trades with "Fresh Wallet" / "Insider Suspect" / "Fresh Whale"
anomaly badges — confirming the signal has real operator demand.
Trade-size thresholds commonly cited: ≥ $10k noteworthy, ≥ $50k
"whale", ≥ $100k major position
([alphascope.app](https://www.alphascope.app/blog/polymarket-whale-tracking-order-flow),
[polymarketscan.org](https://polymarketscan.org/whales)).

## Implementation status

| Component | Path | Status |
|-----------|------|--------|
| Detector | `src/polymarket_insider_tracker/detector/fresh_wallet.py` | **Implemented** — nonce≤5, trade≥$1k, age<48h, confidence score 0.5 base + bonuses for brand-new / very-young / large trade. |
| Wallet profiler | `src/polymarket_insider_tracker/profiler/analyzer.py` | **Implemented** — queries Polygon RPC for nonce + age + balances; caches to Redis (`wallet_profile:{address}`, 5 min TTL). |
| Unit tests | `tests/detector/test_fresh_wallet.py` | **Implemented** — 35 tests covering threshold boundaries and confidence scoring. |
| Persistence | `storage/repos.py::WalletRepository` | **Implemented** — stores wallet_profiles + analyzed_at; 5-min cache layer. |
| Daily aggregation | `alert_daily_rollup` (Phase D) | **Implemented** — `scripts/compute-daily-rollup.py` feeds the daily newsletter. |
| **Gap: age-precision** | | The 48h age bound uses wallet's first Polygon tx. For the Maduro case, the bet landed inside the same day the wallet was created — we'd benefit from a minutes-scale age metric surfaced in the alert payload. |

## Ready-to-run test

```bash
cd /home/ami/AMI-AGENTS/projects/polymarket-insider-tracker
PATH=$AMI_ROOT/.boot-linux/bin:$PATH uv run pytest \
    tests/detector/test_fresh_wallet.py -v
```

(35 tests, ≤ 1 s. Exercises nonce, trade-size, age, and confidence
scoring boundaries.)

For a real-stream smoke, run `scripts/capture-trades.py` as a
sidecar for a 24 h window, then replay it:

```bash
uv run python scripts/capture-trades.py        # sidecar
uv run python -m polymarket_insider_tracker.backtest.replay \
    --capture data/captures/capture-$(date -I -d yesterday).jsonl
```

## Newsletter mock (daily)

```
New wallets, big bets                             (last 24 h, 13:00 UTC)

 1 0x7a3…f91   age 2h  nonce 3    $15,000 BUY YES @ 0.075
              market: "Will Maduro leave office by end of Jan 2026?"
              funding origin: Binance hot wallet (2 hops)

 2 0xbe4…018   age 18h nonce 4     $8,200 BUY YES @ 0.41
              market: "Axiom reveal earlier than April 30?"
              funding origin: unknown 2-year-old contract

 … (top 10 by notional × confidence)

→ Full details + PDF attached. 3 of the 10 wallets cluster-match
  an entity registered in the last 72h (see section 4).
```
