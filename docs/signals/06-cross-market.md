# Signal category 06 — Cross-market consistency

**Taxonomy reference:** SPEC-MARKET-SIGNALS § 3.
**Parent spec:** `docs/SPEC-MARKET-SIGNALS.md`.

Signals that observe the *relationships between markets*, not
individual markets in isolation. In Polymarket, many markets are
explicitly or implicitly related (candidate vs party, same-event
different-date, multiple outcomes of an event). A wallet that
trades one market without touching its logical complement, or a
price relationship that breaks against logical bounds, is
informational.

Mostly **P2–P3**. Building these well requires either curated
relationship graphs or cross-market inference the current
pipeline doesn't have yet.

---

## 06-A. Multi-outcome arithmetic violation

### Definition

For an event with N mutually exclusive outcomes (each is its own
Polymarket market), the sum of implied probabilities should be ≈ 1.
When the sum drifts significantly above or below 1 for a
sustained period, it's either (a) arbitrage capital has left
(retail-thin markets) or (b) informed flow on one outcome
specifically, with the others dragging the sum.

### Theoretical basis

Hard probability constraint. An arbitrageur can guarantee a
risk-free return by buying all outcomes when sum < 0.98 or
selling all when sum > 1.02. That the sum sits outside [0.98,
1.02] for minutes means the arbitrageur can't be bothered —
the market is either too thin, too gas-expensive, or the
informed participant is pricing one outcome with confidence that
*overrides* arbitrage (they know that outcome).

### Computation

```
for each multi-outcome event E:
    markets := E.outcome_markets    # 2..N markets, mutually exclusive
    p_total := sum(last_price(m) for m in markets)
    flag if |p_total - 1.0| > 0.03 for > 15 minutes
    dominant := argmax(markets, key=lambda m: last_price(m) - expected_fair)
```

### Data source requirement

- `gamma-api /events/{slug}/markets` — list of markets belonging
  to an event.
- `gamma-api /markets` — live prices.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | One outcome has tiny liquidity; last-trade price lags | Use midpoint of bid/ask when bid/ask exist; skip outcomes with no book |
| 2 | Event is minutes from resolution — converging to 0 or 1 mechanically | Exclude markets with endDate < 24h |

### Calibration

- `0.03` (3 ¢) threshold; book spreads on liquid events are
  ≤ 2 ¢, so 3 ¢ sum-error is informational.
- 15-min persistence filter.

### Historical precedent

- 2024 Republican primary outcome markets — sum drifted to 1.04
  for 3 days with the Trump market pricing above its fair share;
  retrospectively this was anticipating the DeSantis suspension.

### Priority

**P2.**

### Reliability band (v1)

`medium`.

---

## 06-B. Candidate vs party containment

### Definition

A market for "X wins the 2028 US election" must have probability
≤ "X's party wins the 2028 US election." When the candidate
market trades above its party upper bound, someone is pricing an
edge specific to X that isn't yet in the party market.

### Theoretical basis

Strict logical containment — `P(X wins) ≤ P(X's party wins)`.
Violations are arbitrage gaps the same argument as 06-A applies
to.

### Computation

```
for each (candidate_market, party_market) in CONTAINMENT_PAIRS:
    c := last_price(candidate_market)
    p := last_price(party_market)
    flag if c > p + 0.02 (accounting for book spreads) for > 30 min
```

### Data source requirement

- `containment_pairs.yaml` hand-curated.
- Live `gamma-api` prices.

### Historical precedent

- "Biden wins 2024" and "Democrat wins 2024" briefly had
  Biden > Democrat in the days after the debate — a tell that
  retail hadn't yet re-priced the party market.

### Priority

**P2.**

### Reliability band (v1)

`medium`.

---

## 06-C. Cross-market first-appearance (wallet)

This is the wallet-side variant of cross-market analysis. Fully
specified in [01-E](01-informed-flow.md#01-e-cross-market-first-appearance)
— included here as a cross-reference because it's technically a
cross-market signal.

### Priority

See 01-E. **P1.**

---

## 06-D. Cross-venue arbitrage tell

### Definition

Polymarket is the deepest US-facing prediction-market venue but
not the only one. Kalshi (CFTC-regulated), Manifold, Augur's
remnants, and sometimes a European book all list overlapping
questions. When Polymarket price diverges materially from the
aggregate "other venues" consensus for ≥ 1 hour, it's either
(a) Polymarket liquidity is better and the others are stale, or
(b) Polymarket has a unique flow — informed or manipulative.

### Theoretical basis

Stoikov & Saglam (2009) and subsequent multi-venue arbitrage
literature. Cross-venue arbitrage is well-understood; the
residual divergences *after* arb is done are informational.

### Computation

```
for each Polymarket market M:
    peers := other_venue_markets_for_same_event(M)
    if len(peers) == 0: skip
    peer_price := volume_weighted_mean(p.last_price for p in peers)
    divergence := polymarket_price(M) - peer_price
    flag if |divergence| > 0.04 for > 60 min
```

### Data source requirement

- Kalshi API, Manifold API, etc. — requires operator credentials
  and an ongoing integration.
- `cross_venue_map.yaml` — hand-curated mapping of Polymarket
  markets to their peers elsewhere.

### Priority

**P3 (roadmap).** Requires integration work with each venue.

### Reliability band (v1)

— (not yet shipping).

---

## 06-E. Correlated-markets co-movement break

### Definition

Two markets that should move together do not. Examples:

- "Fed cuts 25 bps" and "Fed cuts 50+ bps" (both negatively correlated with "no change") should move inversely to "no change"; if only one of them does, somebody is trading on a specific scenario.
- "Team A wins game 1" and "Team A wins the series" should move with correlation ≈ 0.5–0.8 (game 1 is a strong conditioning event); a decoupling is informational.

Differs from 04-B (divergence vs comparable markets) in that 06-E
tracks *correlation* (second moment), not *level* (first moment).

### Theoretical basis

Pairs-trading residuals. Gatev, Goetzmann & Rouwenhorst (2006)
identified the informational content of correlated-pair residuals.

### Computation

```
for each (M1, M2) in CORRELATED_PAIRS with historical_rho >= 0.5:
    rolling_rho_1h := Pearson of hourly returns over last 48h
    if |rolling_rho_1h - historical_rho| > 0.4 for > 4h:
        flag (M1, M2) as decoupled
```

### Data source requirement

- `correlated_pairs.yaml` curated.
- 48 h price histories per market.

### Priority

**P2–P3.**

### Reliability band (v1)

`low` — hard to calibrate per-pair thresholds; historical
correlations are volatile in prediction markets with limited
data.

---

## Summary table

| Signal | Tier | Reliability (v1) | Primary use |
|---|---|---|---|
| 06-A Multi-outcome arithmetic | P2 | medium | Standalone line in weekly |
| 06-B Candidate/party containment | P2 | medium | Standalone line in weekly |
| 06-C Cross-market first-appearance | P1 | low-medium | See 01-E |
| 06-D Cross-venue arbitrage | P3 (roadmap) | — | Deferred |
| 06-E Correlated-markets break | P2–P3 | low | Combined only |

## Composition

```
cross_score(window) =
    0.40 * multi_outcome_violation_count_normalised +
    0.40 * containment_break_present +
    0.20 * correlation_break_count_normalised
```

Cross-score weight in the composition is capped at 15 % of max
`combined_score` in v1 — these are sharp but infrequent signals,
and the data required (curated relationship graphs) is behind a
human-curation bottleneck.
