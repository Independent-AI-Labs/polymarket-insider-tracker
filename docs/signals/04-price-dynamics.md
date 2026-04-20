# Signal category 04 — Price dynamics

**Taxonomy reference:** SPEC-MARKET-SIGNALS § 3.
**Parent spec:** `docs/SPEC-MARKET-SIGNALS.md`.

Signals based purely on the market's price trajectory — breakouts,
mean-reversion failures, autocorrelation changes, and divergence
from comparable markets. Weaker than categories 01–03 on their
own but sharp as confirmation signals: when a price break comes
with a Kyle-style informed-flow signature, you know the direction.

---

## 04-A. Directional break

### Definition

Market price crosses a prior-window extremum by ≥ N basis points
and stays above/below that extremum for a minimum persistence.

### Theoretical basis

Breakout strategies in equity markets (Kaufman 1995; Kirkpatrick
& Dahlquist 2010) — price movement beyond a prior range is
statistically correlated with informed flow when volume
accompanies it (Blume, Easley & O'Hara 1994). Prediction markets
inherit the pattern; the "range" is the implied-probability band
the market had settled into.

### Computation

```
prior_window := [now - 24h, now - 1h]
hi := max(prices in prior_window)
lo := min(prices in prior_window)
current := last_trade_price

breakout_up := current > hi + 200_bps and
               median of last 5 min prices > hi
breakout_down := current < lo - 200_bps and
                 median of last 5 min prices < lo
```

`200 bps` (2 ¢ on a prediction market) is the minimum meaningful
break; market-maker spreads are often 50–200 bps so anything
smaller is noise.

### Data source requirement

- `data-api /trades` per market, newest 24 h.
- Alternatively `gamma-api /markets/{id}/priceHistory` if
  available (currently not exposed publicly).

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Thin market where 2 trades constitute the whole window | Require ≥ 20 trades in the prior window |
| 2 | Price manipulation — informed *looks like* informed, informed|uninformed distinction irrelevant | Accept; the signal is still useful |
| 3 | Approaching resolution, price naturally drifts to 0 or 1 | Exclude markets closing within 12 h |

### Calibration

- `200 bps` minimum break width.
- 5-minute persistence ≈ 5 trades worth of confirmation.

### Historical precedent

- Multiple Fed-rate markets on 2024 decision days — break
  triggers fire within seconds of the Fed statement release.

### Priority

**P1.**

### Reliability band (v1)

`low` alone, `medium` combined with category 01 or 02.

---

## 04-B. Implied-probability divergence vs comparable markets

### Definition

Two markets that should logically move together but don't — e.g.
"Trump wins the election" and "Republican wins the election"
should have correlated price paths. A sustained divergence
suggests one of the markets has a participant trading on
information specific to it (e.g. a Trump-specific leak).

### Theoretical basis

Cross-market arbitrage violation. In equity markets this is the
pairs-trading anomaly (Gatev, Goetzmann & Rouwenhorst 2006).
Prediction markets have explicit logical relationships:

- `P(Trump wins) ≤ P(Republican wins)` always
- `P(A wins) + P(B wins) + ... = 1` for mutually exclusive
  candidates
- `P(X in election) = P(X nominated) × P(X wins | nominated)`

When these relationships break by > 300 bps, either arbitrage
capital has left or someone is trading with info not yet in the
correlated market.

### Computation

Requires a **comparable-markets graph** — hand-curated for P1,
potentially auto-derived later.

```
for each (M1, M2) pair in COMPARABLE_MARKETS:
    expected_relation(M1, M2) holds
    actual_delta := |p(M1) - expected_value_given(p(M2))|
    flag if actual_delta > 300_bps for > 30 min
```

Examples of comparable pairs (v1):

- Every (candidate, party) election market ⇒ candidate ≤ party
- Every multi-outcome event's probabilities ⇒ sum = 1
- Every "will X be out by date D" vs "will X be out by date D+7"

### Data source requirement

- `gamma-api /markets` live-price snapshots per market.
- Hand-curated `comparable_markets.yaml`.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Transient arbitrage (< 5 min) — bots are just slow | Persistence ≥ 30 min filter |
| 2 | Market has structurally different resolution criteria | Careful curation; every pair documented |

### Calibration

- `300 bps` divergence threshold.
- 30-min persistence.

### Historical precedent

- 2024 Republican primary — "Trump wins primary" and "Republican
  wins general" diverged by ~ 600 bps for 3 days, right before
  DeSantis suspended.
- Supreme Court nomination markets vs Senate confirmation markets
  2022 — consistent 400 bps divergence.

### Priority

**P2.** Requires curation of `comparable_markets.yaml`.

### Reliability band (v1)

`medium` (when curated), `low` if auto-derived.

---

## 04-C. Intra-window autocorrelation

### Definition

The lag-1 autocorrelation of trade-price changes within a
window. Markets with informed flow have positive autocorrelation
(trends) because the informed trader is walking the price;
uninformed markets have near-zero or slightly negative
autocorrelation (mean reversion between retail tick-takers).

### Theoretical basis

Lo & MacKinlay (1988) "Stock Market Prices Do Not Follow Random
Walks" — informed-flow regimes produce positive autocorrelation;
market-maker-dominated regimes produce the classic slightly-
negative autocorrelation from bid-ask bounce.

### Computation

```
prices := sequence of (ts, price) from trades in window
returns := [prices[i+1] - prices[i] for i in ...]
rho_1 := Pearson(returns[:-1], returns[1:])
```

Flag when `rho_1 ≥ 0.30` over ≥ 100 trades.

### Data source requirement

- `data-api /trades` per market.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Extremely thin market — every trade moves price, AR coefficient is structural | Require ≥ 100 trades |
| 2 | Sequential partial fills of a single large order (not new information, just slicing) | Cross-reference with 02-C; if a single wallet dominates the window, the AR is mechanical |

### Calibration

- `rho_1 ≥ 0.30`. Equity-market informed-regime AR is typically
  0.05–0.15; we widen because prediction-market price steps are
  larger.
- 100-trade floor.

### Historical precedent

- Election primary markets 2024 — sustained AR ≈ 0.4 during the
  week leading up to Iowa.

### Priority

**P2.**

### Reliability band (v1)

`low` — fragile; works only as a confirmation signal.

---

## 04-D. Mean-reversion failure

### Definition

After a large single-print move (≥ 300 bps), the price should
typically partially revert toward the pre-print mid (Easley &
O'Hara 1992 "Time and the Process of Security Price
Adjustment"). When it doesn't — the move persists — that's
informational, not liquidity-shock.

### Theoretical basis

Adverse-selection model. A large uninformed trade gets bid back
by MMs (mean reversion). A large informed trade the MMs don't
fade because they recognise they'd be on the wrong side.

### Computation

```
for each trade t with |price_impact(t, 60s)| >= 300_bps:
    pre_price := median of prices in [t-5min, t-60s]
    post_price := median of prices in [t+5min, t+15min]
    reversion := (post_price - t.price) / (pre_price - t.price)
                ∈ [..., 1]  (1 = fully reverted)

    flag if reversion < 0.25  (i.e. price stayed within 25% of shock size)
```

### Data source requirement

- `data-api /trades`.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Noise in post-print window — few trades | Require ≥ 5 trades in post-window |
| 2 | Consecutive informed trades — compounding, not a single-print failure | Consider 02-A OFI co-signal; distinct phenomenon |

### Calibration

- `300 bps` shock threshold.
- `25 %` reversion floor for flagging failure.

### Historical precedent

- Fredi9999 $1.5M trades — zero reversion across every print.
  Textbook informed-flow signature.

### Priority

**P1.**

### Reliability band (v1)

`medium` alone, `high` as a confirmation of 01-B.

---

## Summary table

| Signal | Tier | Reliability (v1) | Min trades | Primary use |
|---|---|---|---|---|
| 04-A Directional break | P1 | low → medium | 20 prior-window | Combined only |
| 04-B Divergence vs comparable | P2 | medium | — | Confirmation of cross-leak |
| 04-C Autocorrelation | P2 | low | 100 | Confirmation only |
| 04-D Mean-reversion failure | P1 | medium → high | 5 post-print | Combined with 01-B |

## Composition

```
price_score(market, window) =
    0.30 * z(AR_1) +
    0.30 * breakout_present +
    0.20 * reversion_failure_count_normalised +
    0.20 * divergence_bps / 500
```

`price_score` contribution capped at `0.5 * combined_score_max`
in v1 because category 04 has the highest false-positive risk.
