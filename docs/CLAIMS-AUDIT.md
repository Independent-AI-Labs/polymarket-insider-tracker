# README Claims Audit

**Date:** 2026-04-19
**Status:** baseline — regenerate when README is materially edited or detector
defaults change.

Every row below maps a claim in `README.md` to the code path that would make
it true, the value actually baked into code, and a status. The purpose is to
stop the README and the detector stack drifting apart.

**Status key**
- `IMPLEMENTED` — claim matches code as written.
- `THRESHOLD-MISMATCH` — claim is implemented but the numeric threshold the
  README cites differs from the code default.
- `ASPIRATIONAL` — claim has no implementation.
- `ATTRIBUTED` — external fact cited but not exercised by this repo.

## Detection signals

| # | README claim | README line | Code location | Actual value | Status |
|---|--------------|------------|---------------|--------------|--------|
| 1 | "Fresh Wallets: Brand new wallets making large trades" | 29 | `detector/fresh_wallet.py` | — | IMPLEMENTED |
| 2 | "Flags wallets with fewer than 5 lifetime transactions" | 60 | `detector/fresh_wallet.py:19` | `DEFAULT_MAX_NONCE = 5` | IMPLEMENTED |
| 3 | "making trades over $1,000" | 60 | `detector/fresh_wallet.py:18` | `DEFAULT_MIN_TRADE_SIZE = Decimal("1000")` | IMPLEMENTED |
| 4 | "Traces funding source to identify if connected to known entities" | 61 | `profiler/funding.py` + `profiler/entities.py` | max 3 hops via USDC transfer graph | IMPLEMENTED |
| 5 | "Liquidity Impact Analysis: Calculates trade size relative to market depth" | 63-64 | `detector/size_anomaly.py` | — | IMPLEMENTED |
| 6 | "Flags trades consuming more than 2% of visible order book" | 65 | `detector/size_anomaly.py:18` | `DEFAULT_BOOK_THRESHOLD = 0.05` (**5%**, not 2%) | THRESHOLD-MISMATCH → resolved by patching README to 5%. Rationale: code is what actually runs; 2% was aspirational prose. Operators can tighten to 2% per instance via the constructor arg. |
| 7 | "Weights by market category (niche markets score higher)" | 66 | `detector/size_anomaly.py:23` | `NICHE_PRONE_CATEGORIES = {science, tech, finance, other}` | IMPLEMENTED |
| 8 | "Sniper Cluster Detection: DBSCAN clustering" | 68-69 | `detector/sniper.py:68` | `SniperDetector`, default `eps=0.5, min_samples=2, entry_threshold_seconds=300` | IMPLEMENTED (**not persisted** — in-memory only; Phase D adds `sniper_clusters` table) |
| 9 | "Identifies coordinated behavior patterns" | 70 | `detector/sniper.py` | emits `SniperClusterSignal` | IMPLEMENTED |
| 10 | "Event Correlation: Cross-references trading activity with news feeds" | 72-73 | — | no news-feed ingestor, no correlation detector | ASPIRATIONAL → demoted to Roadmap |
| 11 | "Detects positions opened 1-4 hours before related news breaks" | 74 | — | no implementation | ASPIRATIONAL → demoted to Roadmap |
| 12 | "Niche Markets: Activity in low-volume, specific-outcome markets" | 31 | `detector/size_anomaly.py:19` | `DEFAULT_NICHE_VOLUME_THRESHOLD = Decimal("50000")` ($50k daily volume) | IMPLEMENTED |
| 13 | "less than $50k daily volume" (sample alert) | 90 | `detector/size_anomaly.py:19` | matches — $50k | IMPLEMENTED |
| 14 | "Funding Chains: Where wallet funds originated from" | 32 | `profiler/funding.py:33` | `FundingTracer`, 3 USDC-transfer hops default | IMPLEMENTED |

## Sample alert details

| # | README claim (line 80-98) | Code location | Status |
|---|---------------------------|---------------|--------|
| 15 | "Wallet: 0x7a3…f91 (Age: 2 hours, 3 transactions)" — illustrative | — | ILLUSTRATIVE |
| 16 | "Size: $15,000 USDC (8.2% of daily volume)" — illustrative | `detector/size_anomaly.py:17` triggers at 2% of daily volume | ILLUSTRATIVE (above threshold, plausible) |
| 17 | "[x] Fresh Wallet (fewer than 5 transactions lifetime)" | matches row #2 | IMPLEMENTED |
| 18 | "[x] Niche Market (less than $50k daily volume)" | matches row #12 | IMPLEMENTED |
| 19 | "[x] Large Position (more than 2% order book impact)" | see row #6 — actual default is 5% | THRESHOLD-MISMATCH → README patched |
| 20 | "Funding Trail: → 0xdef…789 → Binance Hot Wallet" | `profiler/entities.py` + `profiler/funding.py` | IMPLEMENTED (Binance, Coinbase, Kraken, Uniswap hot-wallet constants exist) |
| 21 | "Confidence: HIGH (3/4 signals triggered)" | `detector/scorer.py:28`, weights `{fresh_wallet: 0.40, size_anomaly: 0.35, niche: 0.25}`; multi-signal bonus 1.2×/1.3× | IMPLEMENTED (3-of-3 triggers `HIGH`; the "4" in 3/4 is illustrative) |

## Architecture diagram claims (lines 40-54)

| # | Diagram component | Implementation | Status |
|---|-------------------|----------------|--------|
| 22 | Polymarket API (real-time) → Wallet Profiler | `ingestor/websocket.py` + `ingestor/clob_client.py` + `profiler/analyzer.py` | IMPLEMENTED |
| 23 | Wallet Profiler → Anomaly Detector | `profiler/analyzer.py` + `detector/fresh_wallet.py` / `size_anomaly.py` / `sniper.py` | IMPLEMENTED |
| 24 | Anomaly Detector "ML + Heuristics" | heuristics only (weighted signal sum in `scorer.py`); **no ML model** | THRESHOLD-MISMATCH (labelling) → README patched to "Heuristics (ML-ready)" |
| 25 | Alert Dispatcher → Discord / Telegram / Email | `alerter/dispatcher.py` + `alerter/channels/{discord,telegram}.py` + newsletter via `himalaya batch send` | IMPLEMENTED |

## "Opportunity" anecdote (lines 10-19)

| # | Claim | Attribution | Status |
|---|-------|-------------|--------|
| 26 | Insider wallet turned $35K into $442K (12.6x) on 2026-01-03 | [@DidiTrading](https://x.com/DidiTrading) | ATTRIBUTED — not exercised by this repo. Phase C adds a replay harness so future editions of the newsletter can confirm whether the current detector stack would have flagged that specific wallet/market. |
| 27 | "five separate alerts before the event occurred" | @DidiTrading | ATTRIBUTED — as above. |

## Resolutions applied in this pass

1. **THRESHOLD-MISMATCH row #6 + #19**: README updated to say "5% of visible order book impact" so the text matches `DEFAULT_BOOK_THRESHOLD = 0.05`. Operators wanting the tighter 2% band can pass `book_threshold=0.02` to `SizeAnomalyDetector`.
2. **THRESHOLD-MISMATCH row #24**: diagram label "ML + Heuristics" softened to "Heuristics (ML-ready)" since no ML model is wired today. Phase C's backtesting harness provides the precision/recall/PnL ground truth that would justify adding an ML layer later.
3. **ASPIRATIONAL rows #10 + #11**: "Event Correlation" demoted from the "Detection Algorithms" list into a new `## Roadmap` section in the README, explicitly tagged as not-yet-built.
4. **ATTRIBUTED rows #26 + #27**: the $35K→$442K anecdote is kept as motivational copy but with a footnote pointing at the cited tweet and a note that Phase C validates (or doesn't) the claim against the current detector stack.

## Maintenance

Re-run this audit whenever:
- `README.md` gains a new claim about detection behaviour, or
- A `DEFAULT_*_THRESHOLD` constant in `detector/` changes, or
- A new detector module lands.

A future CI check should grep the README for `%`/`$` + numeric values and
compare against the `DEFAULT_*` constants; not implemented here.
