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

**Signal layer:** every claim in the body of every cadence is
backed by a signal defined in
[`docs/SPEC-MARKET-SIGNALS.md`](SPEC-MARKET-SIGNALS.md) and its
per-category specs in `docs/signals/`. This spec describes the
*product surface*; the signals spec describes *what the product
reports*. Adding or retiring a signal changes what appears in
sections below without changing the section layouts.

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

### 3.4 Delivery mechanics (summary — authoritative detail in § 10)

Each cadence script (`daily.py / weekly.py / monthly.py` under
`scripts/newsletters/`, sharing `_common.py`) drives
`himalaya batch send` via
`newsletter_common.deliver_via_himalaya(...)`. Rate defaults to
`2/min` (= 120/hr) per REQ-MAIL-127. Scheduler, failure handling,
secrets posture, canary flow, and monitoring are all pinned down
in § 10 below — this sub-section only exists so operators
skimming the cadence chapters know where to look for the rest.

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

## 10. Operations

Authoritative source for *how* the newsletters actually run in
production. Every operator-facing mechanic — scheduler,
pre-flight gates, secrets, canary, monitoring — lives here.

### 10.1 Scheduler

**Choice: `ami-cron`.** Rationale:

- Installed at `~/AMI-AGENTS/.boot-linux/bin/ami-cron`, wraps the
  user crontab with labels + idempotent `add` / `remove`.
- Injects `AMI_ROOT` + PATH so scripts find `himalaya`, `uv`,
  `wkhtmltopdf` without per-job environment boilerplate.
- Supports `ami-cron status <label>` for last-run telemetry.

systemd user timers are a viable alternative but would need
`loginctl enable-linger ami` for post-logout persistence plus
one unit-pair per cadence. No upside over ami-cron for this
workload — explicitly rejected.

### 10.2 Schedules (UTC)

| Cadence | Cron spec | ami-cron label | Command |
|---|---|---|---|
| daily   | `0 13 * * *` | `polymarket-daily`   | `cd $AMI_ROOT/projects/polymarket-insider-tracker && make newsletter-daily` |
| weekly  | `0 8 * * 1`  | `polymarket-weekly`  | `cd $AMI_ROOT/projects/polymarket-insider-tracker && make newsletter-weekly` |
| monthly | `0 9 1 * *`  | `polymarket-monthly` | `cd $AMI_ROOT/projects/polymarket-insider-tracker && make newsletter-monthly` |

Wiring commands (idempotent — safe to re-run):

```bash
ami-cron add "0 13 * * *" \
  "cd $AMI_ROOT/projects/polymarket-insider-tracker && make newsletter-daily" \
  --label polymarket-daily

ami-cron add "0 8 * * 1" \
  "cd $AMI_ROOT/projects/polymarket-insider-tracker && make newsletter-weekly" \
  --label polymarket-weekly

ami-cron add "0 9 1 * *" \
  "cd $AMI_ROOT/projects/polymarket-insider-tracker && make newsletter-monthly" \
  --label polymarket-monthly
```

Schedules MAY shift on operator request; the table above is the
default and any deviation MUST be recorded in the audit log
(§ 14).

### 10.3 Pre-flight gates

Each cadence's `make` target runs these gates **in order**
before touching `himalaya`. Any gate failing short-circuits the
run with a non-zero exit, which ami-cron surfaces to the
monitoring channel (§ 10.9).

1. **DNS probe.** `python3 scripts/dns-probe-patch.py --quiet`
   — exits non-zero if any Polymarket hostname is hijacked.
   Rationale: without gamma + data-api the data builder ships
   empty content.
2. **Database reachable.** Quick `SELECT 1` via the async
   engine. Migrations are NOT auto-applied in production — a
   pending-migration state is a human problem.
3. **Himalaya account loaded.** `himalaya account list` must
   name `polymarket`. Missing means the config file at
   `~/.config/himalaya/config.toml` wasn't installed.
4. **Send-domain DNS sanity.** `dig +short TXT <sending-domain>`
   returns SPF + DKIM + DMARC records (REQ-MAIL-122 / 123 / 124).
   Missing is a soft-fail for the canary period (log WARN), hard
   fail once we open to the public subscriber list.
5. **Ledger reachable.** Query `email_deliveries` for the
   planned `edition_id`. If any row is already `outcome='sent'`,
   the per-row idempotency filter in § 7.5 drops it; if ALL rows
   are already sent, the run exits 0 with an INFO log (nothing
   to do — a re-trigger after success, not an error).

### 10.4 Data-builder lifecycle

Newsletters are **pure readers** of persisted Postgres state.
They do not run captures, replays, or detector passes. The
production pipeline (separate systemd service `polymarket-pipeline`
or ami-cron job — see `docs/RUNBOOK.md`) populates
`alert_daily_rollup`, `sniper_clusters`, `wallet_profiles`,
`funding_transfers` continuously. If the pipeline is down, the
newsletter still ships — with whatever data is there — and the
body honestly reflects the gap ("no alerts this window").

**Explicit anti-pattern:** the cadence scripts MUST NOT block on
a fresh capture + replay. A newsletter that refuses to ship
because upstream ingestion stalled is worse than a newsletter
that ships with a stale window; the first fails silently, the
second tells the reader something is wrong.

### 10.5 Himalaya account + secrets

Account config lives at `config/himalaya-account-polymarket.toml`
in the repo and is installed by a dedicated Makefile target:

```bash
make install-himalaya-account
# → cp config/himalaya-account-polymarket.toml ~/.config/himalaya/config.toml
```

The config's SMTP stanza points at the LAN exim-relay
(`192.168.50.66:2526`, unauthenticated per relay policy). The
`auth.raw` field carries a throwaway password that the relay
ignores but himalaya requires syntactically. No real secret is
stored in the repo.

**If the relay moves to the public internet,** the config must
switch to `auth.type = "keyring"` with the credential stored
via `himalaya account configure polymarket`. Repo-committed
passwords are forbidden.

### 10.6 Canary period

Before opening the public signup form (Phase F §6.2), **every
cadence runs for 14 consecutive days with the targets list
restricted to**:

```yaml
delivery:
  targets:
    - name: "archive"
      email: "archive+polymarket@ami-mail.example"
      enabled: true
```

During canary, an operator reviews the archive inbox after each
run. Sign-off criteria:

- Subject line renders correctly across Gmail web, Gmail
  Android, Outlook web, Apple Mail.
- List-Unsubscribe header produces a one-click unsubscribe UI
  in Gmail's inbox list.
- mail-tester.com score ≥ 9/10 on the canary send.
- No DMARC failures in the sending domain's `rua` inbox for
  seven consecutive days.

Only after these are met is the public subscriber list enabled
by flipping `delivery.use_db_subscribers = true` in
`report-config.yaml`.

### 10.7 Failure + retry behavior

**Cadence-level failures** (DB down, gamma API timeout, etc.):

- ami-cron captures stderr and exits non-zero.
- The monitoring job (§ 10.9) sees the failure and pages.
- Operator re-runs the cadence manually via `make
  newsletter-{cadence}`. Idempotency (§ 7.5) ensures already-sent
  rows aren't re-mailed.

**Per-recipient failures** (himalaya reports `outcome='failed'`
for one row):

- Ledger row written with the relay response string.
- Next scheduled cadence run does NOT auto-retry failed rows —
  a failed send is usually a subscriber-side problem (bounced,
  suppressed), and REQ-MAIL-115 says after three hard bounces
  the subscriber flips to `bounced` status and is dropped from
  future sends.
- For transient failures, an operator can force a retry with
  `make newsletter-daily TARGETS=<email>` — the CLI takes a
  subset.

**Hard rule:** we never auto-retry a send inside the same
cadence run. Retries happen either by manual operator action or
by the next scheduled occurrence. This keeps the blast radius
of a runaway loop bounded.

### 10.8 Boot persistence

`ami-cron` writes to the user crontab, which survives reboots
for user `ami` because the Ubuntu host keeps the
`crond` / `cron` service running at boot. No `enable-linger`
required.

If the host is down at the scheduled firing time, that
firing is missed and the next scheduled firing takes over — we
do NOT attempt to "catch up" missed runs. The missed edition
can be manually triggered via `make newsletter-daily DATE=YYYY-MM-DD`
if the operator chooses.

### 10.9 Monitoring + paging

- **Success path:** each cadence writes a summary line to
  stdout (captured by ami-cron → syslog). No explicit pager.
- **Failure path:** ami-cron's built-in failure capture emits
  to syslog with facility `cron.err`. A separate observer
  (outside this repo — lives in `AMI-STREAMS/ansible/roles/ami_mail/`)
  tails syslog for `polymarket-*` labels and posts to the
  operator's monitoring channel on three consecutive failures
  OR one `outcome='failed'` ratio > 20 %.
- **No on-call rotation.** This is a single-operator product;
  the one operator is the pager target. Adding rotation is
  future work.

### 10.10 Secrets inventory

| Secret | Where it lives | How it's read |
|---|---|---|
| Database password | `.env` → `DATABASE_URL` | `pydantic-settings` at startup |
| Polymarket API creds | `.env` → `POLYMARKET_API_*` (optional) | idem |
| Himalaya SMTP password | `~/.config/himalaya/config.toml` (LAN relay — throwaway; migrates to keyring if relay goes public) | himalaya binary |
| Subscriber `unsubscribe_token` | Postgres `subscribers.unsubscribe_token` | data-file per batch send |
| DKIM private key | `/etc/exim4/dkim/newsletter.key` on the relay | exim signer config |

`.env` is gitignored (§ .gitignore line 74). Any secret outside
this table is a violation and should be flagged to the operator.

---

## 11. Implementation ordering

Implementation split into four phases, each independently
deliverable:

### 11.1 Phase N1 — Daily hardening (ready to ship)

- Retire `send-report.py`'s current top-markets-by-volume
  content in favour of the alert-led body defined in § 4.
- Wire `alert_daily_rollup`, `funding_transfers`,
  `entities.yaml` into a `DailyDataBuilder`.
- Author `scripts/templates/polymarket-daily.html` Tera
  template matching § 4.2.
- Emit `alerts-{date}.csv`; retain `snapshot-{date}.pdf` as
  demoted attachment.
- Add `make newsletter-daily` wired to pre-flight gates
  (§ 10.3).
- Scenario test: given a synthetic 24 h alert window, the
  rendered HTML + CSV match golden snapshots.

### 11.2 Phase N2 — Weekly (deliverable once N1 + `price_at_flag` persistence)

- `WeeklyDataBuilder` producing the five § 5.2 sections.
- `scripts/templates/polymarket-weekly.html` Tera template.
- "Aged callouts" price re-fetch helper against Gamma API.
- Add `make newsletter-weekly`.
- Scenario test: golden-file the Mon-08:00 render for a
  synthetic week.

### 11.3 Phase N3 — Monthly (deliverable once N2 + pydot)

- `MonthlyDataBuilder` for the six § 6.2 sections.
- `scripts/templates/polymarket-monthly.html` Tera template.
- `render_cluster_graph()` helper using pydot → PNG.
- Signal co-occurrence + frequency charts via inline SVG.
- Add `make newsletter-monthly`.
- Scenario test: the full 30-day synthetic month renders
  deterministically (graph layout must be seeded for
  repro).

### 11.4 Phase N4 — Cutover

- Switch `scripts/send-report.py` to dispatch
  `send-report.py {daily, weekly, monthly}`.
- Wire ami-cron schedules per § 10.2.
- Run the 14-day canary per § 10.6 to the archive address.
- Flip `delivery.use_db_subscribers = true` only after canary
  sign-off.

---

## 12. Acceptance criteria

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
- `make newsletter-{cadence}` passes every pre-flight gate in
  § 10.3 on the production host.
- `ami-cron list` shows the cadence's label with the schedule
  from § 10.2.

---

## 13. Out of scope (explicit)

- Precision / recall / hit-rate / P&L-uplift numbers anywhere
  in the product. Deferred until outcome scoring ships.
- Per-recipient personalization beyond `name` and
  `unsubscribe_token`. No per-subscriber interest graphs.
- Real-time alerting via email — out of scope; that's a
  push-notification product, not a newsletter.
- Paid tiers or content gating — everything in every cadence
  goes to every active subscriber who has opted into that
  cadence.
- Multi-operator on-call rotation (§ 10.9). Single-operator
  product for now.

---

## 14. Audit log

- 2026-04-20 — initial spec drafted by claude-operator +
  reviewed by vlad. Supersedes the inline Phase E sketch in
  `docs/IMPLEMENTATION-TODOS.md`.
- 2026-04-20 — added § 10 Operations (scheduler, pre-flight
  gates, canary flow, failure handling, secrets inventory);
  renumbered §§ 11-14. Driven by vlad's correct observation
  that the initial draft was content-heavy and
  operationally thin.
- 2026-04-20 — linked in the SPEC-MARKET-SIGNALS taxonomy (new
  § 1 paragraph). Retired the four non-signals from the PDF
  product per SPEC-MARKET-SIGNALS § 6. Daily PDF is rewritten
  per `docs/IMPLEMENTATION-PLAN-SIGNALS.md` Phase S3 (Option B
  — flagged-activity-log appendix).
