# Section 2 — "High-conviction positions" (Unusual Sizing)

## Thesis

Trades whose notional consumes a disproportionate share of the
market's recent liquidity (≥ 2% of 24-hour volume, ≥ 5% of visible
order-book depth). Informed traders don't hedge — when they believe
a resolution is imminent they take large, one-sided positions and
take the slippage. This section surfaces those moves so readers
can see what large money is actually doing, independent of wallet
age.

## Validated cases

### Case 2.1 — Iran military-strike bets (March 2026)

- **What happened**: Bubblemaps identified a single trader who
  placed "dozens of bets" on US and Israeli military actions
  against Iran, with five-figure wager amounts each and a **93%
  win rate**. Several winning bets landed **hours before** strikes
  that had not been publicly announced.
- **Total profit**: ~$1M since 2024.
- **Why this is sizing, not just fresh-wallet**: the wallet was
  not particularly new; the signal was the *size × timing*
  combination against markets with limited liquidity.
- **Sources**: [Brian Hulela, "Polymarket has an insider trading problem" (Apr 2026)](https://medium.com/@brianhulela/polymarket-has-an-insider-trading-problem-and-the-numbers-prove-it-8a20eaef2d29), [CBS News](https://www.cbsnews.com/news/polymarket-insider-trading-rules-iran-war-venezuela/).

### Case 2.2 — 2024 presidential election "whales"

- **What happened**: per the academic "Anatomy of Polymarket"
  paper (arxiv 2603.03136), a handful of large accounts placed
  ~$30M in bets favouring Trump. 71.8% of traders participated in
  the Trump YES market; 30.7% traded *exclusively* Trump YES,
  i.e. took a concentrated directional position.
- **Volume context**: the 2024 election drew **$3.3B in trading
  volume** on Polymarket — so individual $100k+ positions are
  measurable but not market-moving at the top markets; the
  detector is tuned for the tail, where a $15k trade can be
  ~8% of daily volume (matches the sample alert in the README).
- **Sources**: [arxiv "Anatomy of Polymarket"](https://arxiv.org/html/2603.03136v1), [Fortune Oct 2024](https://fortune.com/crypto/2024/10/30/polymarket-trump-election-crypto-wash-trading-researchers/).

### Threshold discipline

Commercial whale trackers define trade tiers roughly as:

| Tier | Notional | Source |
|------|----------|--------|
| noteworthy | $10k+ | Alphascope |
| whale | $50k+ | Alphascope |
| major | $100k+ | Alphascope |

Our size detector fires on *volume-impact ratio* rather than
absolute dollars, so a $5k bet against a $50k-volume niche market
fires the same alert as a $100k bet against a $4M-volume market.
That's the point — informed sizing is relative.

## Implementation status

| Component | Path | Status |
|-----------|------|--------|
| Detector | `src/polymarket_insider_tracker/detector/size_anomaly.py` | **Implemented** — `DEFAULT_VOLUME_THRESHOLD = 0.02` (2% of 24h volume), `DEFAULT_BOOK_THRESHOLD = 0.05` (5% of order-book depth). Both tunable via constructor args. |
| Market metadata sync | `ingestor/metadata_sync.py` | **Implemented** — caches MarketMetadata in Redis keyed on market_id; `volume24hr`, `liquidityClob`, `bestBid`, `bestAsk` populated from Gamma API. |
| Unit tests | `tests/detector/test_size_anomaly.py` | **Implemented** — threshold boundary cases, bid/ask context. |
| Daily rollup | `alert_daily_rollup` (Phase D) | **Implemented** — signal column = `size_anomaly`; newsletter reads the top markets by alert density. |
| Backtest | `backtest/metrics.py::aggregate_metrics` | **Implemented** — separate precision / pnl_uplift row per signal; `size_anomaly` has its own metrics row in the monthly dashboard. |
| **Gap: pre-announcement correlation** | | The Iran case shows *size + timing vs external events*. Timing is in-scope of the deferred Event Correlation roadmap (`docs/ROADMAP-EVENT-CORRELATION.md`). |

## Ready-to-run test

```bash
cd /home/ami/AMI-AGENTS/projects/polymarket-insider-tracker
PATH=$AMI_ROOT/.boot-linux/bin:$PATH uv run pytest \
    tests/detector/test_size_anomaly.py -v
```

For end-to-end against a recorded stream:

```bash
uv run python -m polymarket_insider_tracker.backtest.replay \
    --capture data/captures/<yyyymmdd>.jsonl
psql -c "
  SELECT signal, hits, misses, precision, pnl_uplift_bps
    FROM detector_metrics
   WHERE signal = 'size_anomaly'
   ORDER BY window_start DESC LIMIT 7;
"
```

## Newsletter mock (daily)

```
High-conviction positions                         (last 24 h, 13:00 UTC)

 1  $48,300 BUY NO @ 0.28   ← 7.1% of 24h volume in market
     market: "Will Operation X begin by Friday?"
     wallet 0xdef…789 (2y old, 500+ txns)

 2  $18,900 BUY YES @ 0.92  ← 12% of order-book depth
     market: "Will FDA approve drug Y before end of April?"
     wallet 0x12a…ab4 (6mo old, 34 txns)  ← also appears in section 3

 … (top 5 by volume-impact × book-impact × weighted_score)
```
