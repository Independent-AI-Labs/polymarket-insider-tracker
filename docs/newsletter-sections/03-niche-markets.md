# Section 3 — "Niche-market targeting" (Niche Markets)

## Thesis

Trading activity concentrated in markets with < $50k daily volume
and narrow outcome spaces. Niche markets are where information
asymmetry is most exploitable — the crowd hasn't sized them up,
liquidity is thin, and anyone with a plausible edge can buy a
whole resolution at a favourable price. Nearly every "famous
Polymarket insider trade" in public reporting is a niche-market
trade in disguise.

## Validated cases

### Case 3.1 — Google "Year in Search" 2025

- **What happened**: a Polymarket trader — wallet handle
  "AlphaRaccoon", on-chain identifier starting `0xafEe…` — bet
  heavily on Google's 2025 *Year in Search* rankings and profited
  nearly $1M. Nearly every outcome was called correctly.
- **Why it's a niche-market case**: Google trend-ranking markets
  are extremely low-volume (< $50k daily) and have dozens of
  narrow outcome tokens per market. One wallet buying a specific
  outcome at the whole-dollar doesn't move top-market stats.
- **Sources**: [Yahoo Finance / BeInCrypto coverage](https://finance.yahoo.com/news/polymarket-trader-makes-1-million-090001027.html), [BeInCrypto](https://beincrypto.com/alleged-google-insider-trade-polymarket/).

### Case 3.2 — Maduro / Iran operations (recurring)

- **What happened**: both the Maduro-removal and Iran-strike
  markets cited in sections 1 and 2 also qualify as niche-market
  cases because the markets themselves had tiny liquidity before
  the bets hit. One $30k trade on the Maduro market was
  effectively the whole market.
- **Why this matters**: the niche-market signal *compounds* with
  the fresh-wallet and size-anomaly signals. `RiskScorer` already
  applies a ×1.2 boost for 2 concurrent signals and ×1.3 for 3
  (`src/polymarket_insider_tracker/detector/scorer.py:28-36`),
  which is why these cases score at the top of the composite
  board.

### Case 3.3 — Category concentration

- The detector explicitly tags categories where niche-information
  asymmetry is most common: `NICHE_PRONE_CATEGORIES =
  frozenset({"science", "tech", "finance", "other"})`
  (`src/polymarket_insider_tracker/detector/size_anomaly.py:23`).
  "Other" is the catch-all for niche political / corporate /
  regulatory markets — which is where the Google, Maduro, and
  Axiom cases all sit.

## Implementation status

| Component | Path | Status |
|-----------|------|--------|
| Detector flag | `src/polymarket_insider_tracker/detector/size_anomaly.py` | **Implemented** — `is_niche_market=True` when `daily_volume < $50k` AND category ∈ NICHE_PRONE_CATEGORIES. |
| Market metadata | `ingestor/metadata_sync.py` | **Implemented** — `volume24hr`, `liquidityClob`, `category` all sync'd from Gamma API. |
| Unit tests | `tests/detector/test_size_anomaly.py` | **Implemented** — niche flag assertions per category. |
| Scorer boost | `detector/scorer.py:28-36` | **Implemented** — multi-signal multiplier. |
| Weekly newsletter | `scripts/weekly-newsletter.py` | **Partial** — weekly template already renders a "top markets by alert density" section; niche-specific call-out can be added by filtering `alert_daily_rollup.signal = 'niche_market'`. |
| **Gap: category refresh** | | `NICHE_PRONE_CATEGORIES` is hard-coded. As Polymarket adds category taxonomy (sports, culture, finance…), we should surface a runtime override in config and back it with a 30-day volume roll-up per category. Low priority. |

## Ready-to-run test

```bash
cd /home/ami/AMI-AGENTS/projects/polymarket-insider-tracker
PATH=$AMI_ROOT/.boot-linux/bin:$PATH uv run pytest \
    tests/detector/test_size_anomaly.py::TestNicheMarketDetection -v
```

To surface niche hits in the weekly newsletter right now:

```sql
SELECT market_id, alert_count, unique_wallets, total_notional
  FROM alert_daily_rollup
 WHERE day >= current_date - interval '7 days'
   AND signal = 'niche_market'
 ORDER BY alert_count DESC
 LIMIT 10;
```

## Newsletter mock (weekly)

```
Niche-market targeting                             (week of 2026-04-13)

 1  0xhot…mkt1  "Will X announce Y by Aug?"       category: other
     6 alerts from 4 distinct wallets · $42,800 notional
     24h volume: $38k (← below niche threshold)

 2  0xcol…mkt7  "Will Drug Z get FDA approval?"   category: science
     3 alerts from 3 wallets · $18,500
     24h volume: $12k

 … (top 10 by alert density in niche markets)

Note: 3 of the 12 wallets in this section were also flagged in
the Fresh Wallets list. Composite score × 1.2 multi-signal boost
applied in the daily alerts — see monthly dashboard for
cross-signal precision.
```
