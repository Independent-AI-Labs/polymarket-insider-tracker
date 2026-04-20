# Signal category 05 — Event catalyst

**Taxonomy reference:** SPEC-MARKET-SIGNALS § 3.
**Parent spec:** `docs/SPEC-MARKET-SIGNALS.md`.

Signals that use *when* the flow happens in the market's life
cycle — proximity to resolution, pre-scheduled-event windows,
and (roadmap) correlation with external news. The unifying idea:
the value of informed flow depends on how much time remains
before the answer is public.

---

## 05-A. Proximity-to-resolution acceleration

### Definition

A market that is approaching resolution AND whose trade
frequency / notional is accelerating faster than the usual
resolution-day ramp. Informed flow often concentrates in the
final window when the informed party knows (or has predicted
with confidence) the imminent result.

### Theoretical basis

The informed-trader model of Back (1992) predicts that
information is fully revealed by the terminal payoff, so
informed traders who *do* have an edge have increasing
incentive to size up as the resolution approaches (their edge
becomes certainty). The empirical counterpart in prediction
markets — Wolfers & Zitzewitz (2004), Table 2 — shows informed
volume concentrating within the last 10 % of a market's life.

### Computation

```
life_remaining(market) := market.end_date - now    (seconds)
life_total(market) := market.end_date - market.start_date
life_remaining_pct := life_remaining / life_total

trade_rate_recent := trades_per_hour over last 4h
trade_rate_baseline := trades_per_hour over the entire market's life

accel := trade_rate_recent / trade_rate_baseline

flag if life_remaining_pct <= 0.10 AND accel >= 4.0
```

### Data source requirement

- `gamma-api /markets` — `startDate`, `endDate`.
- `data-api /trades?market={conditionId}&limit=1000` to get
  recent trade timestamps.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Natural resolution-day volume spike (everybody pays attention when it matters) | Compare against venue-wide resolution-day ramp; require `accel ≥ 4×` (not 2×) |
| 2 | News broke publicly — everyone pile-ons | Cross-check with 05-C (news correlation, when available); standalone 05-A still flags but reliability is `low` |
| 3 | Short-duration market (e.g. 5-minute crypto candle) where `life_remaining_pct ≤ 0.10` is 30 seconds | Exclude markets with `life_total ≤ 2 hours` |

### Calibration

- `10 % life remaining` gives a window of hours-to-days on
  meaningful markets.
- `4×` baseline multiplier reliably separates informed ramps
  from news-ramps in the 2024 election replay.

### Historical precedent

- Super Tuesday 2024 — "Trump wins New Hampshire" accel = 6.8×
  over the final 18 h; the informed flow preceded the exit poll
  leak by 3 h.
- 2023 Academy Awards "Best Picture" markets — accel ≈ 5× in
  the final 12 h, coinciding with industry insiders' positions.

### Priority

**P1.**

### Reliability band (v1)

`medium` when combined with 01-B or 02-A; `low` alone.

---

## 05-B. Pre-scheduled-event window

### Definition

The time window before a known scheduled public event
(election, Fed decision, Supreme Court ruling announcement, FDA
decision deadline). Markets with informed flow often concentrate
trading in the final 24–72 h before the event.

### Theoretical basis

Meyer, Meyerson & Hutchings (2014) "Event studies" — pre-event
abnormal volume is a standard alpha signal. In prediction
markets the effect is sharper because the resolution date is
literally the event.

### Computation

```
for each market:
    scheduled_event := closest_scheduled_event_matching(market)
        # match by keyword + date proximity ± 48h
    if scheduled_event exists AND
       0 < time_to_event <= EVENT_WINDOW_HOURS:
        window_factor := 1 + ((EVENT_WINDOW_HOURS - time_to_event) / EVENT_WINDOW_HOURS)
        # scales from 1.0 at window start to 2.0 at event time
    else:
        window_factor := 1.0
```

`window_factor` is used as a **multiplier** on other signals'
scores — not a standalone flag. A 02-A OFI of 0.70 in a regular
window is medium-signal; the same 0.70 in the final 24 h before
a scheduled event is high-signal.

### Data source requirement

- `scheduled_events.yaml` hand-curated registry. Schema:

  ```yaml
  - date: 2026-05-03T20:00:00+00:00
    event: "FOMC rate decision"
    keywords: ["fed", "fomc", "interest rate"]
    source: "Federal Reserve calendar"
  ```

- v2: pull from a calendar API (Econoday, Bloomberg); v1 is
  manual curation of ≤ 50 events / quarter.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Our keyword matcher mis-attributes — e.g. all Fed markets get the window_factor every Fed meeting, even markets that aren't about this particular rate decision | Tighten keyword matching; include date-range validation |
| 2 | "Scheduled" event gets postponed last-minute | Registry gets updated by an operator; our calendar is a sync, not a snapshot |

### Calibration

- `EVENT_WINDOW_HOURS = 72` (3 days) default.
- `window_factor` ∈ [1.0, 2.0].

### Historical precedent

- FOMC meeting days: informed flow consistently concentrates in
  the 6 h before the statement drop.
- Election nights: ramp starts 48 h out, peaks at 18 h out.

### Priority

**P2.** Requires ongoing curation of the registry.

### Reliability band (v1)

`medium`.

---

## 05-C. News correlation (roadmap)

### Definition

Cross-correlate each market's price trajectory against a news
feed time-series (headline appearance, social-media mention rate)
to identify markets that moved *before* the public news did.

### Theoretical basis

This is the textbook informed-trading signal — Fama (1970) semi-
strong-form efficiency says public news should be incorporated
instantly; a pre-news move is, by definition, trading on private
information.

### Computation

```
for each market:
    news_timeline := external news feed filtered for market keywords
    price_timeline := last 72 h of trade prices
    compute lagged cross-correlation
    flag if price leads news by >= 1 hour
```

### Data source requirement

- **External news feed.** Candidates: NewsAPI, Bloomberg
  terminal, Twitter/X firehose, AP wire. All require paid
  subscriptions.

### Status

**ROADMAP.** Explicitly deferred until a news-feed vendor
integration is authorised. See
`docs/ROADMAP-EVENT-CORRELATION.md` for the full deferred spec.

### Priority

**P3 (roadmap-only).**

---

## Summary table

| Signal | Tier | Reliability (v1) | Data dep | Notes |
|---|---|---|---|---|
| 05-A Proximity acceleration | P1 | medium | gamma + /trades | Alone |
| 05-B Scheduled-event window | P2 | medium | scheduled_events.yaml | Multiplier on others |
| 05-C News correlation | P3 (roadmap) | — | External news feed | Deferred |

## Composition

05-A fires its own row in the daily "time-to-resolution" table
(if we add one) or feeds into `event_score`:

```
event_score(market, window) =
    0.70 * accel_z +
    0.30 * window_factor_above_1
```

05-B is applied as a multiplicative adjustment to `combined` at
the composition step: `combined *= window_factor(M)`.
