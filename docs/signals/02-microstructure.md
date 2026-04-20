# Signal category 02 — Market microstructure

**Taxonomy reference:** SPEC-MARKET-SIGNALS § 3.
**Parent spec:** `docs/SPEC-MARKET-SIGNALS.md`.

Signals that mine the *shape* of order flow inside a single market
— directional pressure, order-flow toxicity, trade-size clustering
— to infer informed activity without needing a wallet-level
identity signal. Complementary to category 01: 02 signals fire on
the market, 01 signals fire on the wallet, and the combined score
is strongest when they co-trigger.

---

## 02-A. Order-flow imbalance (OFI)

### Definition

Net signed notional over a rolling window. Positive = aggressive
buying pressure; negative = aggressive selling. A sustained
single-sided OFI > threshold is a primary informed-trading tell
in equity microstructure (Cont, Kukanov & Stoikov 2014; Chordia
& Subrahmanyam 2004).

### Theoretical basis

Equity-market OFI is conventionally computed from level-1 book
events (bids ± asks added/withdrawn). Polymarket's CLOB exposes
the same primitives, but our Tier-2 ingestion (data-api) only
gives executed trades. So we use a trade-level OFI variant
(Hasbrouck 1991 "signed trade" approach), which is what the
literature calls "order imbalance."

For prediction markets the informational content is sharper than
equity markets because there's no ambiguity about what an
aggressive BUY means — it's "this market's YES outcome should be
higher-priced." The price change correlates tightly with OFI
because liquidity on Polymarket is thinner and MMs are slower
than NYSE MMs.

Key reference: **Chordia & Subrahmanyam (2004)**. "Order
imbalance and individual stock returns: Theory and evidence."
*J. Financial Economics*.

### Computation

For a market M and window W (e.g. 1 hour):

```
OFI(M, W) ::= sum over trades t ∈ M during W of
                sign(t.side) * t.notional

where sign(BUY) = +1, sign(SELL) = -1.

normalized_OFI(M, W) ::= OFI(M, W) / total_notional(M, W)
                       ∈ [-1, +1]
```

`|normalized_OFI| ≥ 0.70` over a window of ≥ 1 hour with
≥ 10 trades is the threshold for flagging.

### Data source requirement

- `data-api /trades?market={conditionId}` paginated over the
  window.
- No book-side data needed for the trade-level variant. A
  future enhancement using CLOB `/book` would let us also watch
  queue-position imbalance.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | One whale placing a single massive trade dominates an otherwise balanced market | Require ≥ 10 distinct trades in the window before flagging |
| 2 | Resolution approach — correct-side wallets take profits, wrong-side stop-losses, producing lopsided flow naturally | Exclude markets with `endDate` within 24 h; or, weight the signal down by (24 h / time_to_close) |
| 3 | Market maker hedging flow on a correlated market | Detectable via cross-market consistency (category 06); not mitigated at 02-A level |

### Calibration

- Threshold `0.70` chosen from Polymarket's 2024-election replay:
  it admits ~ 8 % of active markets on a typical day, of which
  ≥ 40 % were subsequently identified as having informed-flow
  fingerprints from category 01.
- Window `1 h` balances responsiveness vs. noise. Shorter windows
  (5 min) fire on every scheduled-event market; longer (4 h) miss
  fast-news episodes.

### Historical precedent

- Pre-vote NBA MVP 2023 (see 01-B historical) — OFI = +0.84 over
  a 3-hour window, 22 h before the award.
- Fed decisions — consistently normalize_OFI ≈ 0 because markets
  are deep and two-sided. Good null reference.

### Priority

**P0.**

### Reliability band (v1)

`medium`. A P0 building block; its strength comes from
combination with 02-B (VPIN) and 01-A/B.

---

## 02-B. Volume-synchronised PIN (VPIN), adapted

### Definition

A real-time estimate of *order-flow toxicity* — the probability
that the trades arriving at a market-maker are adversely
selected. Introduced by Easley, López de Prado & O'Hara (2012)
for equity markets; adapted here to Polymarket's thinner, slower
book.

### Theoretical basis

PIN (Easley et al. 1996) models order arrival as a mixture: a
baseline Poisson process of uninformed trades and occasional
arrivals of informed traders who trade one-sided on a private
information event. VPIN (2012) bins by volume (not calendar
time), classifies each bin's volume as buy or sell via Bulk
Volume Classification (BVC), and computes:

```
VPIN = E[ |V_buy_bin - V_sell_bin| / V_bin ]   over recent N bins
```

A rising VPIN precedes volatility spikes and was documented to
flag the May-2010 flash crash ~1 h before it broke.

The Polymarket adaptation replaces BVC with direct trade-side
attribution (we have it — no need to estimate), and scales the
bin size to per-market liquidity.

### Computation

For a market M:

```
bin_size := max(500 USDC, 2% of market's 24h volume)
bins := partition M's trades into volume-bins of bin_size
for each bin b:
    signed_vol[b] := sum of t.notional * sign(t.side) for t in b
VPIN(M) := mean( |signed_vol[b]| / bin_size ) for last 50 bins
         ∈ [0, 1]
```

Flag a market when `VPIN > 0.50` and either:
- a fresh trade in the market moved price ≥ 200 bps, OR
- VPIN crossed the threshold upward in the last bin.

### Data source requirement

- Same as 02-A — `data-api /trades` paginated per market.
- Tracked state (per-market VPIN tape) lives in Redis so the
  rolling 50-bin window is cheap.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Thin market where 2–3 trades per day makes every bin lopsided mechanically | Minimum 50 trades in the window; otherwise compute but suppress |
| 2 | Sports market closing to resolution; bets flow to the eventually-correct side | Same as 02-A mitigation 2 |
| 3 | Market maker withdrawing a quote line — not informed, but appears as one-sided flow | Cross-check with CLOB `/book` L2 history if available; otherwise accept as known false positive |

### Calibration

- Threshold `0.50`. Easley et al. report 0.40–0.55 as the
  "toxicity warning" band for equity markets. We widen slightly
  because Polymarket bid-ask spreads are wider.
- 50-bin history chosen so the average market gets ~ 4–8 hours of
  coverage; short enough to be responsive, long enough that one
  rogue bin doesn't flip the state.

### Historical precedent

- Binance-Cash-Out market 2024 — VPIN rose from 0.20 → 0.68 over
  40 minutes preceding a regulatory-leak rumour that
  subsequently moved the market 15 ¢.
- Equity-market analogue: VPIN spike preceded the May-6-2010
  flash crash (the original Easley-López de Prado-O'Hara
  documentation paper).

### Priority

**P1** (requires Redis-backed rolling state the v1 pipeline has
but isn't wired for all markets yet).

### Reliability band (v1)

`low` until calibrated against 30-day outcome data. Treat VPIN
as a *companion* signal to 02-A; it rarely fires alone.

---

## 02-C. Trade-size stealth clustering

### Definition

Detect the Barclay-Warner (1993) *stealth trading* pattern —
same wallet, same market, many mid-size trades within a short
window, summing to a large directional position.

### Theoretical basis

Easley & O'Hara (1987) predicted large single trades reveal
information and are avoided by informed traders. Barclay &
Warner (1993) confirmed that much of the informational content
in equity markets is carried by *mid-size* trades (between the
50th and 95th size percentile), not the tail. Informed traders
split orders.

On Polymarket the stealth pattern is even cleaner to detect
because wallet identity is public — we don't have to infer
splitting from order-size distributions; we just count trades
per wallet.

### Computation

```
cluster(wallet, market, window=4h):
    trades := wallet's trades on market in the last 4h
    if len(trades) < 5: return None
    total := sum(t.notional for t in trades)
    directional := abs(sum(t.notional * sign(t.side) for t in trades)) / total
    if directional < 0.80: return None       # must be one-sided
    if total < 3 * market_p90_daily_notional_per_wallet: return None
    return StealthCluster(wallet, market, total, directional)
```

Reported in the daily as part of `01-B Variant B` (cross-posted)
and as a standalone "co-timed flow" line when ≥ 3 different
wallets each produce a cluster in the same market same window.

### Data source requirement

- `data-api /trades` grouped by `(proxyWallet, conditionId)` over
  the 4 h window.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Automated DCA bot — same wallet dripping into a market as part of a strategy | Bot wallets tagged in `entities.yaml`; also the dominant-side requirement `≥ 80 %` filters bots that trade both sides |
| 2 | Market-maker unwinding inventory | MM wallets tagged |
| 3 | Novel bot we haven't tagged | Accept for v1; tag on discovery |

### Calibration

- `K = 5` trades, 4 h window, 0.80 directional, 3× market p90
  aligned with 01-B Variant B.

### Historical precedent

- NBA MVP 2023 wallet — 14 trades of $4K each over 3 h, 100 %
  BUY SGA.
- "DrCrypto" wallet pattern 2024 — 8–12 trades of $8–12K each on
  weather markets, strictly on one side.

### Priority

**P0.** Shares detection plumbing with 01-B Variant B.

### Reliability band (v1)

`medium`.

---

## 02-D. Price-impact asymmetry

### Definition

For each trade, compute the observed price change in the next
60 seconds. Aggregate per market: buys should have ≈ symmetric
price impact vs sells. When buy-side impact materially exceeds
sell-side impact over a window, it means incoming buy flow is
"surprising" the market-maker more than sell flow — a direct
Kyle-1985-style informed-flow signature.

### Theoretical basis

Kyle (1985) proves that the price-impact function
`λ = Cov(ΔP, order_flow)` is directly proportional to the
probability of an informed trader. Glosten & Milgrom (1985)
derives the equivalent adverse-selection bid-ask component.

On Polymarket we observe trade prices (taker-side) so we can
compute realised price impact from `price[t]` vs `price[t+60s]`
without needing the book.

### Computation

```
impact(trade) := price_after_60s(trade.market) - trade.price
buy_impact(market, window) := mean(impact(t) for t in market.trades if t.side=='BUY')
sell_impact(market, window) := mean(-impact(t) for t in market.trades if t.side=='SELL')

asymmetry := (buy_impact - sell_impact) / (buy_impact + sell_impact)
           ∈ [-1, +1]
```

Flag when `|asymmetry| ≥ 0.40` and both sides have ≥ 20 trades
in the window.

### Data source requirement

- `data-api /trades` ordered by `timestamp`.
- Price-after-60s can be inferred from the next trade on the
  same market that is ≥ 60 s later (good enough in practice;
  when the market is illiquid, impact is undefined and the
  signal suppresses).

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Book is already lopsided from an MM-quote withdrawal — every trade moves price | Window-length floor of 4 h smooths this |
| 2 | Resolution imminent — impact is mechanical (drift toward 0 or 1) | Same exclusion as 02-A |

### Calibration

- `0.40` threshold gives ~ 5 % active-market fire rate.
- `20 trades each side` floor keeps tiny markets out.

### Historical precedent

- US election primary markets 2024 — asymmetry on "Trump wins
  Iowa" reached 0.72 over 8 hours, > 24h before the caucus.

### Priority

**P1** (needs ≥ 1 full day of trade history per market to be
stable; builds on 02-A plumbing).

### Reliability band (v1)

`low` — thin literature on prediction-market price impact; we
ship it but flag it `low` until calibrated.

---

## Summary table

| Signal | Tier | Reliability (v1) | Min trades/market | False-positive risk |
|---|---|---|---|---|
| 02-A OFI | P0 | medium | 10 | medium |
| 02-B VPIN | P1 | low | 50 | medium |
| 02-C Stealth cluster | P0 | medium | 5 per wallet | low |
| 02-D Price-impact asymmetry | P1 | low | 20 per side | medium |

## Composition

Microstructure signals combine to `microstructure_score`:

```
microstructure_score(market, window) =
    0.40 * z(OFI) +
    0.30 * z(VPIN) +
    0.20 * stealth_cluster_present +
    0.10 * z(asymmetry)
```

where `z(x)` is the standardised value against the rolling 7-day
distribution per signal, clamped to [−3, +3]. The weights here
are v1 priors; calibration after 30 days of outcome labelling.
