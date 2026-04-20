# SPEC-NEWSLETTERS-POLYMARKET

**Status:** accepted, pre-implementation
**Authors:** claude-operator, vlad
**Date:** 2026-04-20
**Supersedes:** the aspirational newsletter copy inline in
`scripts/report-config.yaml` and the `## Phase E` stubs in
`docs/IMPLEMENTATION-TODOS.md`.
**Depends on:** REQ-MAIL §§ 10 / 11 / 12 (public-sending
compliance), SPEC-MAIL §§ 6 / 7 / 8-ext (subscriber registry,
headers + DNS, bounce handling).

---

## 1. Purpose

Define the content, structure, data sources, and delivery
mechanics of the three Polymarket insider-tracker newsletters
(daily / weekly / monthly) as a product surface, in a way that is
deliverable with the current schema and does **not** depend on
market-outcome scoring. Outcome scoring remains a future
capability; every claim this spec makes about subscriber value
must be deliverable without it.

Non-goals:

- Paid tiers / payment integration.
- Event correlation with external news feeds (roadmap-only per
  `docs/ROADMAP-EVENT-CORRELATION.md`).
- A/B testing infrastructure.
- Multi-tenant sharding of the subscriber list.

---

## 2. Product position

A subscriber to this newsletter gets **an observation log**, not a
signal service. The product promise is:

> We watch every Polymarket trade, fingerprint the patterns that
> correlate with informed flow (fresh wallets, size anomalies,
> niche markets, funding-origin clusters), and each day / week /
> month we publish what we saw. You decide what it means.

The product explicitly does **not** promise:

- Precision or hit-rate claims until outcome scoring lands.
- Timely alerts — cadence is the product, not low-latency
  notification. Subscribers who need seconds-scale alerts are
  not this audience.
- Trade recommendations. Every row is an observation; the
  subscriber draws the conclusion.

This framing is load-bearing: it's what lets us ship value today
without inventing calibration we can't back with data.

---

## 3. Cadences — structural rules

### 3.1 Universal structure (all three cadences)

Every email MUST carry, in this order:

1. `<h1>` headline: cadence + date range (e.g.
   `Polymarket Watchlist — 2026-04-20`).
2. A **single-sentence lead** auto-generated from the data — the
   single most striking fact in the window.
3. Body sections per §§ 4 / 5 / 6 below.
4. Footer partial (REQ-MAIL-125) — legal name, postal address,
   reason-for-receipt, unsubscribe link.

Every email MUST carry MIME headers:

- `List-Unsubscribe: <mailto:...>, <https://.../unsubscribe?token=...>`
- `List-Unsubscribe-Post: List-Unsubscribe=One-Click`
- `Message-ID` — unique per (recipient, edition).

Per REQ-MAIL-120 / 121 / 127 / 128.

### 3.2 Rendering pipeline

HTML is the source of truth. The PDF attachment (where present)
is generated from the same HTML via wkhtmltopdf. Never author
twice. Tera templates live in `scripts/templates/` and reuse the
existing `partials/unsubscribe_footer.html`.

### 3.3 Style constraints

- **Max body width:** 640 px (per `report-config.yaml::style.max_width`).
- **Inline numeric claims deep-link** to the Polymarket profile or
  market page for verifiability.
- **No emoji in body copy** unless the user explicitly requests.
- **Inline images:** only funding-origin bar (daily) and the
  cluster network (monthly). Both base64-embedded so Gmail's
  "display images" gate doesn't break the narrative.
- **Gmail clip threshold is 102 KB** — daily and weekly bodies
  MUST render under that. Monthly MAY exceed and rely on the PDF.

### 3.4 Delivery mechanics

Each cadence script (`daily.py / weekly.py / monthly.py` under
`scripts/newsletters/`, sharing `_common.py`) drives
`himalaya batch send` via
`newsletter_common.deliver_via_himalaya(...)`. Rate defaults to
`2/min` (= 120/hr) per REQ-MAIL-127.

---

## 4. Daily — *Overnight Watchlist*

### 4.1 Slot

- **Subject:** `[AMI] Polymarket Watchlist — {date}`
- **Scheduled:** 13:00 UTC daily via ami-cron.
- **Data window:** previous 24 h, closed at scheduled run time.
- **Reader question:** "What moved in the last 24 h that I should
  react to *today*?"
- **Target read time:** ≤ 90 s.
- **Body size budget:** ≤ 100 KB.

### 4.2 Body — section-by-section

#### 4.2.1 Headline (one sentence, auto-generated)

Template: `"{N} fresh wallets opened positions ≥ ${threshold}; heaviest flow on '{top_market_title}'."`

Data: count rows in `alert_daily_rollup` WHERE `day = yesterday`
AND `signal = 'fresh_wallet'`; sum `total_notional` grouped by
`market_id`, pick top.

Fallback when zero alerts: `"Quiet night — no fresh-wallet
flow above the ${threshold} threshold."` (Must still ship;
silence is a signal.)

#### 4.2.2 Today's alpha wallets — top 5

Table. Columns:

| wallet | market | side | notional | why flagged | ⟶ |
|---|---|---|---|---|---|
| `0x9af3…e410` | Will X happen? | BUY | $42,000 | `fresh + size` | link |

- **Ranking:** top 5 alerts by combined weighted score within the
  24 h window.
- **Source:** join `alert_daily_rollup` → `wallet_profiles` →
  live trade record.
- **`why flagged`** is the literal signal names (`fresh_wallet`,
  `size_anomaly`, `niche_market`, `combined`) joined with `+`.
- **Deep-links:** each wallet shortform links to
  `https://polymarket.com/profile/{address}`; market title
  links to `https://polymarket.com/event/{event_slug}`.

#### 4.2.3 Markets with the most insider-shape flow — top 3

Table. Columns:

| market | alerts | unique wallets | notional | ⟶ |
|---|---|---|---|---|

- **Ranking:** top 3 by `alert_daily_rollup.alert_count`
  descending within the window.
- **Source:** `alert_daily_rollup` grouped by `market_id`.

#### 4.2.4 Funding-origin bar — inline SVG

One horizontal bar chart, 4-6 categories:

`Binance / Coinbase / Kraken / other CEX / on-chain only / unknown`

- **Source:** `funding_transfers` joined back to flagged wallets
  from this window, bucketed by `from_address` against the
  `entities.yaml` allowlist.
- **Format:** inline SVG, ≤ 2 KB. No external assets.
- **Purpose:** answers "where is this money coming from?" at a
  glance.

#### 4.2.5 Footer

Standard `partials/unsubscribe_footer.html` — legal name, postal
address, reason-for-receipt, unsubscribe link.

### 4.3 Attachments

| File | Format | Size | Purpose |
|---|---|---|---|
| `alerts-{date}.csv` | CSV | 5-50 KB | Every raw alert from the 24 h window. Columns: `ts, wallet, market_id, market_slug, signal, weighted_score, side, size, notional, price, market_close_at`. Power users sort / filter / re-score. |
| `snapshot-{date}.pdf` | PDF | 200-400 KB | Existing `market-snapshot-{date}.pdf` — demoted from primary content to "zoom-out reference." |

### 4.4 Data sources (current schema)

All deliverable today without schema changes:

- `alert_daily_rollup` (day, market_id, signal, alert_count,
  unique_wallets, total_notional)
- `wallet_profiles` (address, nonce_at_capture, first_seen, …)
- `funding_transfers` (to_address, from_address, amount_usdc)
- `entities.yaml` for CEX address allowlist

### 4.5 Dependencies / gaps

- None. Ships today.

---

## 5. Weekly — *Insider Digest*

### 5.1 Slot

- **Subject:** `[AMI] Polymarket — Weekly Digest {week_end}`
- **Scheduled:** Monday 08:00 UTC.
- **Data window:** Mon 00:00 → Sun 23:59 UTC of the completed week.
- **Reader question:** "What patterns held across the week?
  Which wallets are worth tracking forward?"
- **Target read time:** ~ 5 min.
- **Body size budget:** ≤ 100 KB.

### 5.2 Body — section-by-section

#### 5.2.1 Week summary (one paragraph)

Auto-generated from aggregates: unique flagged wallets, new
clusters detected, markets with ≥ N days of sustained flagged
flow, biggest single-trade notional. Numbers inline; no table.

#### 5.2.2 Wallet of the Week — narrative profile

One wallet, ~ 3-5 paragraphs. Selection rule: highest sum of
`weighted_score` across the week's alerts. Content:

- First-seen date (from `wallet_profiles.first_seen`).
- Funding origin chain — walk backwards from first funding
  transfer, at most 3 hops, via `funding_transfers`.
- Every market they touched this week (table: market, side,
  notional, our flag timestamp).
- Largest single position + current open exposure.
- Plain-prose description of what they might be watching.

Tone: observational, not editorial. No "this wallet is likely
insider" language — let the data do the work.

#### 5.2.3 New sniper clusters

One row per `sniper_clusters` entry whose `detected_at` falls
within this week. Columns:

| cluster id | members | avg Δ entry | common markets | confidence |
|---|---|---|---|---|

Each member row expands to a nested list: `0xwallet (first trade
<date>, funded from <origin>)`.

#### 5.2.4 Stories in motion — top 5 still-open markets

Markets where flagged flow accumulated during the week AND
`market.closed = false`. One-line narrative per market:

> *"Will Country X hold elections by Dec 2026" attracted 14
> flagged wallets ($680K notional) over Thu-Sun, heavily
> Binance-funded; still 4 months to resolution."*

Derived programmatically; the narrative is a template fill, not
prose generation.

#### 5.2.5 Aged callouts — price-movement record

This is the weekly's trust-earning section. Table of every
wallet+market pair flagged exactly 7 days ago, showing:

| market | our flag @ | price then | price now | Δ % | status |
|---|---|---|---|---|---|
| Will X happen | 2026-04-13 | 0.42 | 0.61 | +45 % | open |

- **Not a hit/miss claim** — just a price-movement record.
- **Source:** `trade.price` is stored at alert time; "price now"
  fetched from Gamma API at newsletter-render time.
- **Honesty note in the section header:**
  *"This is observed price movement, not a precision claim. We
  don't yet score outcomes; weekly recap lands when that ships."*

#### 5.2.6 Footer

Standard.

### 5.3 Attachments

| File | Format | Size | Purpose |
|---|---|---|---|
| `wallets-week-{week_end}.csv` | CSV | 50-200 KB | Every unique flagged wallet, aggregated: appearance count, total notional, signals tripped, markets touched, funding origin. |
| `clusters-week-{week_end}.csv` | CSV | 5-20 KB | New clusters + members (one row per wallet, joined on cluster_id). |
| `weekly-digest-{week_end}.pdf` | PDF | ~ 500 KB | Full email re-rendered as printable. Archival. |

### 5.4 Data sources

All deliverable today:

- `alert_daily_rollup` aggregated over 7 days.
- `sniper_clusters` + `sniper_cluster_members`.
- `wallet_profiles`.
- `funding_transfers`.
- Gamma API (current-price re-fetch).

### 5.5 Dependencies / gaps

- Must persist `price_at_flag` on each alert record. Confirm
  present in the current detector-write path; extend if missing.

---

## 6. Monthly — *The Ledger*

### 6.1 Slot

- **Subject:** `[AMI] Polymarket — The Ledger, {month}`
- **Scheduled:** 1st of month, 09:00 UTC.
- **Data window:** full calendar month just ended.
- **Reader question:** "Who are the persistent operators? Has
  the insider ecosystem changed structurally?"
- **Target read time:** 20-30 min; keepable reference doc.
- **Body size budget:** email body may exceed 100 KB (lean on PDF
  attachment for the full content if truncated).

### 6.2 Body — section-by-section

#### 6.2.1 Macro

- Totals: flagged notional, unique wallets, new clusters.
- Week-over-week alert-count sparkline (inline SVG).
- Funding-origin composition (inline SVG, same style as daily
  but month-aggregated).
- Top 5 markets by flagged notional.

#### 6.2.2 Persistent operators

Wallets that landed on the daily watchlist ≥ 5 times this month.

| wallet | appearances | total notional | funding origin | markets touched |
|---|---|---|---|---|

- **Source:** count wallet appearances across the 30 daily
  watchlists; reconstruct from `alert_daily_rollup`.
- **Purpose:** the "cast of characters" — the value readers
  come back for monthly.

#### 6.2.3 Cluster landscape — inline PNG

One rendered network graph.

- **Nodes** = wallets active in any `sniper_clusters` entry this
  month; **edges** = co-market presence; **node size** = sum
  notional; **node color** = funding-origin bucket.
- **Renderer:** graphviz via the `pydot` library — cheap
  dependency, deterministic output.
- **Size cap:** 200 KB PNG. Beyond that, render top-50-nodes
  subgraph and link to the full graph in the PDF attachment.

#### 6.2.4 Funding archaeology

Bar chart + narrative: where are flagged wallets first funded?
Month-over-month delta on each CEX origin. Highlights any
single exchange becoming a disproportionate insider origin.

- **Source:** walk `funding_transfers` to the earliest USDC
  in-flow per flagged wallet; bucket by `from_address`.
- **M-o-M delta:** requires last month's data — stored in the
  prior edition's PDF attachment, not in a dedicated table.
  For the first month, display "(no prior data)" honestly.

#### 6.2.5 Signal mechanics (*deliberate substitute for calibration*)

Until outcome scoring lands, the monthly's trust-earning
section is "here's what our lens sees":

- **Signal frequency distribution:** bar chart of how often each
  of the 4 detectors fired this month.
- **Signal co-occurrence heatmap:** 4×4 matrix — when detector X
  fires on wallet W in market M, what's the probability
  detector Y also fires for the same (W, M)? Renders as inline
  SVG.
- **Threshold suggestions:** for each signal, report
  `(firings this month, firings per wallet-market-day at the
  10th / 50th / 90th percentile of notional)`. Lets readers
  judge threshold tightness themselves.

Header of this section carries an honesty note:

> *"These are observations about our detectors, not precision
> claims. Precision / recall numbers ship when outcome scoring
> lands — see § 6.2.7."*

#### 6.2.6 What the ledger will add next month

One paragraph. Roadmap preview: when outcome scoring / Tier-3
chain indexer lands, the ledger adds precision, recall, and
P&L uplift numbers. Gives subscribers a concrete thing to
look forward to; keeps the product honest about its current
limits.

#### 6.2.7 Footer

Standard.

### 6.3 Attachments

| File | Format | Size | Purpose |
|---|---|---|---|
| `wallets-month-{month}.csv` | CSV | 200 KB - 2 MB | Every wallet that triggered any signal this month + full aggregates. |
| `clusters-month-{month}.csv` | CSV | 10-50 KB | All clusters + members. |
| `cluster-graph-{month}.png` | PNG | ≤ 200 KB | High-res cluster graph. |
| `the-ledger-{month}.pdf` | PDF | 2-5 MB | Full rendered report, 20-30 pages. The archival deliverable. |

### 6.4 Data sources

All deliverable today:

- `alert_daily_rollup` aggregated over 30 days.
- `detector_metrics` (signal frequency + co-occurrence are
  computable from `alerts_total` per signal).
- `sniper_clusters`.
- `funding_transfers`.
- `wallet_profiles`.

### 6.5 Dependencies / gaps

- **`pydot` + `graphviz` system package** — add to
  `pyproject.toml` dev dependencies and the CI + runtime
  installations.
- **Month-over-month delta** — requires reading the prior
  month's CSV attachment from the edition ledger's attachments
  cache. Acceptable alternative: compute on the fly from
  `alert_daily_rollup` which already covers the prior month.

---

## 7. Cross-cadence rules

### 7.1 Observation, not claim

No cadence emits precision, recall, hit-rate, or accuracy
numbers until outcome scoring exists. Every metric the
newsletters publish is an **observation count**, not an
**outcome label**. The weekly's "aged callouts" and the
monthly's "signal mechanics" are the deliberate substitutes
and both carry honesty headers.

### 7.2 Deep-linkability

Every wallet, market, and claim in the body walks back to a
live source via a link. No derived numbers that can't be
independently verified.

### 7.3 CSV attachments are primary data

The CSVs are what a skeptical reader uses to audit our
narrative. They MUST contain the raw alert / wallet / cluster
rows the body summarized, not a pre-filtered view. Anyone who
wants to disagree with our top-5 ranking can re-rank from the
CSV.

### 7.4 Subject-line alignment

Subject lines match the sender domain's registrable domain
(REQ-MAIL-126). No fake `Re:`, no deceptive origin. The
`[AMI]` prefix is permitted and identifies the sender to the
recipient, matching the operator's established brand.

### 7.5 Idempotency

Every send is identified by `edition_id = "{cadence}-{date}"`
(REQ-MAIL-118). `email_deliveries` is queried pre-send; any
row already `outcome='sent'` for the edition is dropped.
Implemented in `deliver_via_himalaya`.

### 7.6 Autoescape

All subscriber-derived strings (name, unsubscribe URL, any
comment text) are autoescaped by Tera (REQ-MAIL-128).
Sender-controlled template inputs (wallet addresses, market
titles, our own analysis prose) may use `| safe` — the
provenance is captured in the data builder, not in the
template.

---

## 8. Data sources matrix

| Source | Daily | Weekly | Monthly | Notes |
|---|---|---|---|---|
| `alert_daily_rollup` | ✅ | ✅ | ✅ | primary fact table for all cadences |
| `wallet_profiles` | ✅ | ✅ | ✅ | wallet identity, first-seen |
| `funding_transfers` | ✅ | ✅ | ✅ | funding-origin reasoning |
| `sniper_clusters` + members | — | ✅ | ✅ | surface clusters to readers |
| `detector_metrics` | — | — | ✅ | signal mechanics section |
| Gamma API (live) | ✅ | ✅ | ✅ | market metadata + current price |
| `entities.yaml` | ✅ | ✅ | ✅ | CEX allowlist for funding bucketing |
| Chain RPC | — | — | — | **Tier 3 — gated on paid RPC** |

---

## 9. Failure modes + graceful degradation

| Failure | Impact | Handling |
|---|---|---|
| Gamma API unreachable | Aged-callouts section (weekly) can't fetch current price | Section renders with `"—"` for price-now; section header notes the gap |
| `funding_transfers` empty for a wallet | Funding-origin column is `"unknown"` | Bucket counts `unknown` explicitly; never hides the gap |
| Zero alerts in the window | Daily headline shows silence message (§ 4.2.1); body sections render "no rows this window" | Email still ships |
| Cluster graph render fails | Monthly cluster section | Falls back to tabular cluster list; logs at ERROR |
| himalaya exits non-zero | Delivery failure | Ledger row with `outcome='failed'`; next run's idempotency query lets a retry go through |
| PDF renderer (wkhtmltopdf) missing | Attachment absent | Email body still ships; PDF attachment row in ledger marked `attachment_missing` |

---

## 10. Implementation ordering

Implementation split into three phases, each independently
deliverable:

### 10.1 Phase N1 — Daily hardening (ready to ship)

- Retire `send-report.py`'s current top-markets-by-volume
  content in favour of the alert-led body defined in § 4.
- Wire `alert_daily_rollup`, `funding_transfers`,
  `entities.yaml` into a `DailyDataBuilder`.
- Author `scripts/templates/polymarket-daily.html` Tera
  template matching § 4.2.
- Emit `alerts-{date}.csv`; retain `snapshot-{date}.pdf` as
  demoted attachment.
- Scenario test: given a synthetic 24 h alert window, the
  rendered HTML + CSV match golden snapshots.

### 10.2 Phase N2 — Weekly (deliverable once N1 + `price_at_flag` persistence)

- `WeeklyDataBuilder` producing the five § 5.2 sections.
- `scripts/templates/polymarket-weekly.html` Tera template.
- "Aged callouts" price re-fetch helper against Gamma API.
- Scenario test: golden-file the Mon-08:00 render for a
  synthetic week.

### 10.3 Phase N3 — Monthly (deliverable once N2 + pydot)

- `MonthlyDataBuilder` for the six § 6.2 sections.
- `scripts/templates/polymarket-monthly.html` Tera template.
- `render_cluster_graph()` helper using pydot → PNG.
- Signal co-occurrence + frequency charts via inline SVG.
- Scenario test: the full 30-day synthetic month renders
  deterministically (graph layout must be seeded for
  repro).

### 10.4 Phase N4 — Cutover

- Switch `scripts/send-report.py` to dispatch
  `send-report.py {daily, weekly, monthly}`.
- Wire ami-cron schedules per § 4.1 / 5.1 / 6.1.
- Run a 14-day canary to a single archive address before
  opening the public signup form.

---

## 11. Acceptance criteria

A cadence is considered shipped when all of the following are
true:

- Golden-file scenario test passes (HTML + CSV).
- `himalaya batch send --dry-run` against a canned subscriber
  list produces the expected MIME structure (headers per § 3.1,
  attachments per § 4.3 / 5.3 / 6.3).
- A canary send to a dedicated archive mailbox renders cleanly
  in Gmail, Outlook web, and Apple Mail.
- mail-tester.com score ≥ 9/10.
- `email_deliveries` ledger records one row per recipient with
  `outcome='sent'` and a parseable `message_id`.
- The "honesty note" in the weekly (§ 5.2.5) and monthly
  (§ 6.2.5) headers renders verbatim — no claims drift from
  spec-approved copy.

---

## 12. Out of scope (explicit)

- Precision / recall / hit-rate / P&L-uplift numbers anywhere
  in the product. Deferred until outcome scoring ships.
- Per-recipient personalization beyond `name` and
  `unsubscribe_token`. No per-subscriber interest graphs.
- Real-time alerting via email — out of scope; that's a
  push-notification product, not a newsletter.
- Paid tiers or content gating — everything in every cadence
  goes to every active subscriber who has opted into that
  cadence.

---

## 13. Audit log

- 2026-04-20 — initial spec drafted by claude-operator +
  reviewed by vlad. Supersedes the inline Phase E sketch in
  `docs/IMPLEMENTATION-TODOS.md`.
