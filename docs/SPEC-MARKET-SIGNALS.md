# SPEC-MARKET-SIGNALS — Polymarket insider-flow detection taxonomy

**Status:** draft for review
**Authors:** claude-operator + vlad
**Date:** 2026-04-20
**Companion docs:** `docs/signals/*.md` (per-category signal specs),
`docs/SPEC-DATA-SOURCES.md`, `docs/IMPLEMENTATION-PLAN-SIGNALS.md`,
`docs/SPEC-NEWSLETTERS-POLYMARKET.md` (consumer spec).

---

## 1. Purpose

Define the full signal taxonomy this product emits. Every numerical
claim in a newsletter (daily / weekly / monthly), every wallet or
market that gets a badge, every "insider-shape flow" mention — all
of it traces back to one of the signals catalogued here.

Each signal has a documented:

- **Definition** — what it *claims* to measure.
- **Theoretical basis** — why the claim is plausible; typically an
  academic market-microstructure or prediction-market reference.
- **Computation** — formula + data inputs, precise enough that a
  second engineer could re-implement it from this doc.
- **False-positive modes** — every way the signal fires without
  there being real informed flow.
- **Calibration** — how we chose the thresholds, how we revisit them.
- **Historical precedent** — the Polymarket (or comparable) case
  that motivates inclusion.
- **Priority tier** — P0 (ship day one) through P3 (roadmap).

The taxonomy is modular on purpose. Adding a signal never requires
editing the detector core; it's a new entry with its own write-up
in `docs/signals/` and a rollup row in `alert_daily_rollup`.

## 2. Context: what makes Polymarket different

Most market-microstructure literature comes from equity markets
(NYSE / Nasdaq / futures). Polymarket has three structural
properties that either sharpen or invalidate those references:

1. **Full on-chain visibility.** Every fill is a Polygon transaction;
   `proxyWallet` is public. Equity markets hide beneficiary
   identities behind brokers and TRF pools. Polymarket's equivalent
   of "SEC Form 13F" is a live SQL query. Signals that need
   counterparty identity (e.g. "who else does this wallet trade
   with?") are cheap here and expensive there.
2. **Binary terminal payoff.** Every market resolves to 0 or 1. This
   makes price = implied probability, so an absolute price move of
   4 ¢ on a 0.50 market is +800 bps implied-probability shift
   worth the same dollars as the same move on a 0.20 market. Many
   equity signals (returns, volatility, beta) translate literally;
   a few (dividend-adjusted) don't apply.
3. **Asymmetric information about the underlying event.** In equity
   markets the informed trader has superior views on future cash
   flows. In prediction markets the informed trader often *knows
   the answer* — a campaign staffer who knows internal polling,
   a journalist who has seen the exit poll, an insider on a
   regulatory committee. This is qualitatively stronger than equity
   informed flow and produces sharper behavioural signatures
   (Wolfers & Zitzewitz 2004; Page & Siemroth 2020).

Polymarket also has a **different liquidity profile** per market.
Elite political markets can carry $50M+ books; long-tail sports
props carry $5K. A signal that works on the first can produce
nonsense on the second, so every per-market threshold scales with
the market's own liquidity.

## 3. Signal taxonomy

Six categories. Each has its own spec doc in `docs/signals/`:

| # | Category | Doc | What it detects | Priority |
|---|---|---|---|---|
| 1 | Informed-flow fingerprints | [01-informed-flow.md](signals/01-informed-flow.md) | Fresh wallet, unusual size, funding origin, entity-tagged wallet, cross-market first-appearance | P0 |
| 2 | Market microstructure | [02-microstructure.md](signals/02-microstructure.md) | Order-flow imbalance, VPIN-adapted toxicity, stealth-trading size clustering, price-impact asymmetry | P0–P1 |
| 3 | Volume + liquidity | [03-volume-liquidity.md](signals/03-volume-liquidity.md) | Volume velocity, taker/maker split, book-depth imbalance, thin-book ratio | P0 |
| 4 | Price dynamics | [04-price-dynamics.md](signals/04-price-dynamics.md) | Directional break, mean-reversion, intra-window autocorrelation, implied-probability divergence vs comparable markets | P1 |
| 5 | Event catalyst | [05-event-catalyst.md](signals/05-event-catalyst.md) | Proximity-to-resolution acceleration, pre-scheduled-event windows, news correlation | P1–P2 |
| 6 | Cross-market consistency | [06-cross-market.md](signals/06-cross-market.md) | Multi-outcome event arithmetic, correlated-markets co-movement, cross-venue arbitrage tell | P2–P3 |

## 4. Priority tiers

- **P0 — daily newsletter launch.** Must ship in the first
  production daily. Usable with the current data sources only
  (data-api + gamma-api + on-chain via Tier-3 when available).
- **P1 — weekly launch gate.** Can accumulate data for 5+ days
  before becoming stable. Needed before the weekly retrospective
  ships.
- **P2 — monthly launch.** Signals whose calibration requires a
  calendar month or external data (news feeds, scheduled-event
  lists).
- **P3 — roadmap.** Explicitly deferred; requires integrations not
  currently planned (e.g. options-market implied-probability
  cross-check, ML-based anomaly detection).

Every signal in the per-category docs is labelled with its tier.

## 5. Composition rules

Signals combine into a `combined_score` per (wallet, market, window)
triple. The composition is:

```
combined = w_flow * informed_flow_score
         + w_micro * microstructure_score
         + w_vol * volume_score
         + w_price * price_score
         + w_event * event_score
         + w_cross * cross_score
```

Weights `w_*` start equal (`1/6` each) for v1 — no pretence of
optimal calibration until we have outcome-labelled data per
`docs/signals/CALIBRATION.md` (future). A wallet ranks onto the
"alpha wallets" daily table when its `combined` crosses a threshold
that by construction admits the top-~N_per_day_target wallets on
the rolling 7-day distribution, so the daily is volume-stable.

Signals that are categorically weaker than others (e.g.
cross-market consistency contributing ≤ 15 % of max combined) are
still reported in per-signal sections but don't push a row into the
"top N" table on their own.

## 6. Non-signals (explicitly)

These look like signals but aren't — listed here so future
contributors don't propose them again:

- **"Near-certain" markets** (price < 0.05 or > 0.95). A
  near-certain market is one the crowd has already priced; absent
  a rapid re-pricing it carries no informed-flow tell. The old
  report-config.yaml "near_certain" observation rule is retired.
- **"Markets by deepest liquidity"**. Ranks markets where market
  makers have parked the most capital — i.e. the *most predictable*
  markets. Inversely correlated with where informed flow shows up.
- **"Recently created markets"**. These are overwhelmingly
  auto-generated 5-minute crypto-candle bot markets. Zero
  insider-tracking value.
- **"Thin book ratio alone"**. A 24h-volume ÷ liquidity ratio > 8
  is a *precondition* for a price-impact signal, not a signal on
  its own. Inclusion as its own observation line creates noise.

All four are currently or were previously in `scripts/report-config.yaml`.
The daily and PDF rewrites in `docs/IMPLEMENTATION-PLAN-SIGNALS.md`
remove them.

## 7. Reliability framework

Every signal carries a **reliability band** — `high / medium / low`
— based on:

- Historical replay performance (once we have labelled outcomes).
- Theoretical soundness (does the academic reference back it?).
- False-positive-mode count + severity.

For v1 we use conservative bands (most signals start `medium` or
`low`), and only promote to `high` once a detector passes a 30-day
rolling precision check (see `docs/signals/CALIBRATION.md`).

The newsletter body surfaces reliability bands explicitly —
`**(medium)**` next to each signal tag — so a reader never
mistakes an untuned signal for a calibrated one.

## 8. Academic references (authoritative)

- **Kyle, A. S.** (1985). "Continuous Auctions and Insider
  Trading." *Econometrica* 53(6). Foundational model of
  informed-trader price impact.
- **Glosten, L. R. & Milgrom, P. R.** (1985). "Bid, Ask and
  Transaction Prices in a Specialist Market." *J. Financial
  Economics* 14. Adverse-selection spread model.
- **Easley, D. & O'Hara, M.** (1987). "Price, Trade Size, and
  Information in Securities Markets." *J. Financial Economics* 19.
  Large-size trades and information.
- **Barclay, M. J. & Warner, J. B.** (1993). "Stealth Trading
  and Volatility." *J. Financial Economics* 34. Mid-size clustering
  for concealing information.
- **Easley, D., Kiefer, N. M., O'Hara, M., & Paperman, J. B.**
  (1996). "Liquidity, Information, and Infrequently Traded Stocks."
  *J. Finance* 51. PIN — probability of informed trading.
- **Easley, D., López de Prado, M., & O'Hara, M.** (2012). "Flow
  Toxicity and Liquidity in a High-Frequency World." *Review of
  Financial Studies* 25. VPIN.
- **Wolfers, J. & Zitzewitz, E.** (2004). "Prediction Markets."
  *J. Economic Perspectives* 18(2). Survey.
- **Hanson, R.** (2007). "Logarithmic Market Scoring Rules for
  Modular Combinatorial Information Aggregation."
- **Page, L. & Siemroth, C.** (2020). "How Much Information Is
  Incorporated in Financial Markets? A Prediction Market Study."
  *Management Science*.

Per-signal docs cite the subset of these that actually underpin
their math. The full list is above so a reviewer can audit coverage
in one place.

## 9. Change management

Adding / modifying / retiring a signal:

1. Edit (or create) the signal's doc under `docs/signals/`.
2. Update the taxonomy table in § 3 of this file.
3. Add an entry to the audit log in § 11 with date + author +
   one-line summary.
4. Implementation lives in `src/polymarket_insider_tracker/detector/`
   with scenario test covering at least one positive case + one
   false-positive mode per signal.

Retiring a signal is a first-class operation — the taxonomy entry
gets marked `RETIRED (date)` with a link to the audit-log entry
explaining why. Retired signals are NOT deleted from this doc so
future contributors learn what didn't work.

## 10. Out of scope

- Automated trade execution on the strength of a signal. This
  project is strictly observational; the newsletter delivers
  intelligence to a human reader who decides.
- ML / deep-learning anomaly detectors. Deferred until we have
  the outcome-labelled dataset § 7 needs for calibration.
- Signals that require non-public data (e.g. broker-side order
  flow). Polymarket's public on-chain feed plus its gamma-api and
  data-api are the substrate.
- Signals dependent on news-feed integrations beyond trivial
  `resolution_event_date` lookups. See § 5 of
  [05-event-catalyst.md](signals/05-event-catalyst.md) for the
  scoped integration that ships.

## 11. Audit log

- 2026-04-20 — initial taxonomy drafted. Claude-operator + vlad.
  Retires "near-certain", "top-liquidity", "recently-created",
  and "thin-book-ratio-alone" from prior report content.
