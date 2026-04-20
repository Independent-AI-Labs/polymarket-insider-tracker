# Signal category 03 — Volume + liquidity

**Taxonomy reference:** SPEC-MARKET-SIGNALS § 3.
**Parent spec:** `docs/SPEC-MARKET-SIGNALS.md`.

Signals that track *how much money is moving* and *where it came
from* — volume velocity, taker-vs-maker split, book-depth
imbalance, and the thin-book ratio that makes any of the above
mean something.

Volume alone is a weak signal (retail pile-ons are volume too),
but volume-against-baseline OR volume-relative-to-liquidity is
sharp. Every signal in this file normalises against the market's
own historical distribution or its current depth.

---

## 03-A. Volume velocity

### Definition

24-hour volume divided by the market's all-time daily average —
a multiplicative factor that captures "this market just woke up."

### Theoretical basis

Standard momentum anomaly (Jegadeesh & Titman 1993, 2001) applied
per-market. In prediction markets the informational
interpretation is stronger: a market with a stable 30-day volume
profile that suddenly triples is responding to either a public
catalyst (news) or a private one (informed flow). Cross-referenced
with category 05 (event catalyst), we separate the two.

### Computation

```
days_active(market) := max((now - market.start_date) / 86400, 1)
baseline_daily(market) := market.total_volume / days_active
velocity(market) := market.volume_24h / baseline_daily
```

Flag when `velocity ≥ 3.0` AND `days_active ≥ 3`.

The `days_active ≥ 3` floor excludes newly-created markets whose
"baseline" is meaningless.

### Data source requirement

- `gamma-api /markets?condition_ids=...` — `volume24hr`,
  `volumeNum` (all-time), `startDate`.
- One API call per daily rollup batch; cheap.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Market has a natural cyclical pattern (weekly sports market with match-day spike) | Extend baseline to 7-day average when `days_active ≥ 14` |
| 2 | Daily Polymarket-wide volume spike lifts all markets | Compare velocity against the venue-wide velocity: flag only when market velocity exceeds venue velocity by 2× |
| 3 | New but high-profile market: 3 days old + its first big news cycle | Accept as a true positive — "first big news" is a catalyst worth flagging |

### Calibration

- Threshold `3.0`. At lower values (1.5–2.0) the signal fires on
  every other market daily.
- Future calibration: use percentile over the current venue-wide
  velocity distribution rather than a fixed multiplier.

### Historical precedent

- 2024 US election aftermath markets — "Will Biden drop out?"
  jumped to velocity ≈ 11× the day after the Atlanta debate.
- Venezuela 2024 — "Maduro out by year-end" hit velocity ≈ 7×
  the day of the disputed election count.

### Priority

**P0.** Currently shipping in the daily.

### Reliability band (v1)

`medium`.

---

## 03-B. Taker-vs-maker split

### Definition

Ratio of aggressive (taker) notional to passive (maker) notional
on a market. A market that's 80 % taker is one where orders are
*being hit* repeatedly — informed flow hitting posted quotes —
as opposed to 80 % maker which is market-makers rebalancing with
minimal urgency.

### Theoretical basis

Foucault, Pagano & Röell (2013) "Market Liquidity" — the
informed trader demands liquidity (hits the quote), the uninformed
can provide it. High taker share ⇒ high informed presence.

### Computation

For each trade on Polymarket, CLOB order type exposes whether the
order was a marketable limit (taker) or sat on the book (maker).
Accessible via `data-api /trades` field inspection:

```
taker_ratio(market, window) :=
    sum(t.notional for t in trades if t.order_type == 'taker') /
    sum(t.notional for t in trades)
```

Flag when `taker_ratio ≥ 0.75` over ≥ 50 trades.

### Data source requirement

- `data-api /trades` — verify `order_type` / `orderType` field is
  present on responses; if not, fall back to 02-A OFI which is
  partially redundant.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Markets with no posted bids/asks — every trade is a taker of the residual queue | Require both sides of book to have > $500 depth (CLOB `/book` call, cached 5 min) |
| 2 | Resolution drift (see 02-A#2) | Same exclusion |

### Calibration

- Threshold `0.75` from equity-market literature (0.70 baseline)
  adjusted up slightly for prediction-market noise.
- Window ≥ 50 trades.

### Historical precedent

- Pre-news crypto markets — taker ratio sustained at 0.80+ for 2
  hours before regulatory news breaks, consistently.

### Priority

**P1** (requires verifying the `order_type` field is on data-api
responses — spec check, not code).

### Reliability band (v1)

`medium`.

---

## 03-C. Book-depth imbalance

### Definition

Ratio of top-of-book size on YES side vs NO side from the CLOB
orderbook. A persistent imbalance (e.g. 5× more YES bid depth
than NO ask depth) indicates MMs pulling from one side —
typically because they're seeing informed flow on that side.

### Theoretical basis

Cont, Kukanov & Stoikov (2014) "The price impact of order book
events." Book depth imbalance is a well-documented short-horizon
return predictor. Market makers adjust their quoted sizes as
adverse-selection signals arrive.

### Computation

```
book := CLOB GET /book?market=<condition_id>
ylevel := book.bids[0].size    (best YES bid size)
nlevel := book.asks[0].size    (best NO ask size; equivalently
                                "best YES ask" since No = 1 - Yes)
depth_imbalance := abs(ylevel - nlevel) / (ylevel + nlevel)
                ∈ [0, 1]
```

Flag when `depth_imbalance ≥ 0.60` AND the imbalance is
persistent over ≥ 3 book snapshots at 1-minute intervals.

### Data source requirement

- `clob.polymarket.com /book` — works via our Tier-1 WS
  subscription, which already materialises book state in-memory.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Single MM has withdrawn temporarily (server restart, rebalancing) | Persistence requirement (3 snapshots / 3 min) filters |
| 2 | Market about to resolve | Same exclusion |

### Calibration

- `0.60` threshold from Cont-Kukanov-Stoikov experiments.

### Historical precedent

- 2024 election primary markets — YES-side book depth routinely
  10× NO-side in the 6 h window before a caucus, as MMs pulled
  quotes anticipating informed flow.

### Priority

**P1** (requires CLOB WS book-state wiring, currently partial).

### Reliability band (v1)

`medium`.

---

## 03-D. Thin-book ratio (gate, not a signal on its own)

### Definition

24-hour volume divided by current book liquidity. A high ratio
(e.g. 8×+) means a single trade moves price a lot — *necessary*
for any other signal in this category to be actionable.

### Theoretical basis

Mechanical. Amihud (2002) "illiquidity measure." If the book is
so thick that a $100K trade doesn't move price, none of the
category 02 or 03 signals have teeth.

### Computation

```
thin_book_ratio(market) := volume_24h / (liquidity_Clob + 1e-9)
```

Used as a **gate** — signals 02-A, 02-B, 03-B don't flag markets
with `thin_book_ratio < 2` because the price-impact interpretation
breaks down.

### Data source requirement

- `gamma-api /markets` — `volume24hr`, `liquidityClob`.

### Why it is NOT a standalone signal

The legacy observation rule "thin-book markets where 24h vol > 8×
liquidity" emits one line per qualifying market. But the ratio is
a *precondition* for interesting signals, not a signal itself.
Surfacing it standalone creates noise (e.g. a brand-new market
with tiny book naturally has a huge ratio).

This is retired per SPEC-MARKET-SIGNALS § 6.

### Priority

**P0 as a gate.** Not surfaced in the newsletter body.

---

## Summary table

| Signal | Tier | Reliability (v1) | Primary use |
|---|---|---|---|
| 03-A Volume velocity | P0 | medium | Standalone section in daily |
| 03-B Taker/maker split | P1 | medium | Combined with 02-A |
| 03-C Book depth imbalance | P1 | medium | Combined with 02-A / 02-D |
| 03-D Thin-book ratio | P0 (gate) | n/a | Precondition gate only |

## Composition

```
volume_score(market, window) =
    0.50 * z(velocity) +
    0.30 * z(taker_ratio) +
    0.20 * depth_imbalance_flag
```

Gated by `thin_book_ratio ≥ 2` — below the gate, volume_score is
suppressed regardless of components.
