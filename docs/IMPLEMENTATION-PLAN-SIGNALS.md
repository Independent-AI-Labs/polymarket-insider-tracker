# IMPLEMENTATION-PLAN-SIGNALS

**Status:** draft, awaiting sign-off
**Authors:** claude-operator + vlad
**Date:** 2026-04-20
**Parent:** `docs/SPEC-MARKET-SIGNALS.md`, `docs/SPEC-DATA-SOURCES.md`

Rollout plan for the signal taxonomy. Each phase has a closed
scope, a dependency manifest, and a clear exit gate — nothing
ships downstream until the gate above closes. Every change here
updates `docs/IMPLEMENTATION-TODOS.md` (Phase 15+).

---

## 1. Execution sequence

```
Phase S0 — Retire noise    →   ships immediately, blocks nothing
                ↓
Phase S1 — P0 signals      →   unlocks daily rewrite
                ↓
Phase S2 — Daily rewrite   →   first production daily with real signals
                ↓
Phase S3 — PDF rewrite (Option B)
                ↓
Phase S4 — P1 signals      →   unlocks weekly
                ↓
Phase S5 — Weekly ship
                ↓
Phase S6 — P2 signals + monthly ship
                ↓
Phase S7 — Tier-3 enabler (paid RPC) + deferred signals
```

---

## Phase S0 — Retire noise signals (no code, just delete)

**Goal:** purge the four signals explicitly listed in
SPEC-MARKET-SIGNALS § 6 as non-signals:

- `near_certain` observations
- `top liquidity` section of the PDF
- `recently created markets` section of the PDF
- `thin book ratio` as standalone observation (kept as gate only)

**Scope:**

- `scripts/send-report.py`: delete the `near_certain` observation
  rule, delete sections 2/3/4 from the PDF generation.
- `scripts/report-config.yaml`: remove corresponding `observations`
  and `sections` blocks. Keep only the one top-24h-volume section
  as a fallback until the PDF is fully rewritten (Phase S3).
- Tests: delete obsolete snapshot tests.

**Gate:**

- `make newsletter-test` still sends.
- Fresh PDF contains only top-24h-volume table (interim) + no
  observations block.

**Owner:** claude-operator. **Est:** 1 pass.

---

## Phase S1 — P0 signals

Implements every signal tagged P0 in the taxonomy — minimum
viable insider-flow daily newsletter. All run against live
data-api + gamma-api. No chain RPC required.

**Signals delivered:**

| # | Signal | Source doc | Deps |
|---|---|---|---|
| 01-A | Fresh wallet (Polymarket-first-trade variant) | 01-informed-flow.md § 01-A | /trades |
| 01-B | Unusual size | 01-informed-flow.md § 01-B | /trades |
| 02-A | Order-flow imbalance | 02-microstructure.md § 02-A | /trades |
| 02-C | Stealth clustering | 02-microstructure.md § 02-C | /trades |
| 03-A | Volume velocity | 03-volume-liquidity.md § 03-A | gamma |
| 03-D | Thin-book gate | 03-volume-liquidity.md § 03-D | gamma |

**Scope:**

- New `src/polymarket_insider_tracker/detector/signals/` package,
  one module per signal.
- Each module exposes a pure function:
  `def compute(trades: list[Trade], markets: dict[str, Market])
  -> list[SignalHit]`.
- `detector/composer.py` — applies the composition rules from
  SPEC § 5 and emits per-(wallet, market, window) scored rows.
- Repository writes to `alert_daily_rollup` (already exists).

**Infrastructure:**

- Add `src/polymarket_insider_tracker/pipeline/signals_pass.py`
  — a scheduled task (ami-cron, every 15 min) that:
  1. Paginates data-api for the last 24 h at `filterAmount=10000`.
  2. Bulk-fetches gamma metadata for all condition_ids.
  3. Runs every P0 signal.
  4. Writes rollup rows.

**Gate:**

- 24 h of uptime produces ≥ 100 rollup rows.
- Scenario tests per signal pass.
- Integration test: feed 2024-election replay capture →
  Fredi9999 cluster flagged by ≥ 2 of (01-A, 01-B, 02-C).

**Owner:** claude-operator. **Est:** ~2 working sessions.

---

## Phase S2 — Daily rewrite (consume the signals)

**Goal:** `scripts/newsletter-daily.py` reads from
`alert_daily_rollup` and renders the signal-led body per
SPEC-NEWSLETTERS-POLYMARKET § 4.

**Scope:**

- Replace the current `_context_from_live` single-pass with a
  `_context_from_rollup` path — exits the "live data-api
  fallback" code path permanently once Phase S1 is stable.
- Headline renders the single strongest signal of the day
  (highest `combined_score`).
- `source_label` flips from `"data-api-live"` to
  `"detector-rollup"` — template adapts headings.
- `alerts-{date}.csv` attachment is now derived from
  `alert_daily_rollup` + `wallet_profiles` (when available),
  not from a raw /trades pull.

**Gate:**

- `make newsletter-daily` sends, rendered body has every signal
  section with ≥ 1 row.
- Advisor-persona rubric pass per
  `docs/NEWSLETTER-REVIEW-RUBRIC.md`.

**Est:** 1 session.

---

## Phase S3 — PDF rewrite (Option B)

**Goal:** the PDF becomes a single coherent reference doc, not
a multi-section mechanical dump.

**New layout:**

1. Cover page — date + window + big three numbers
   (trade count ≥ $10K, unique wallets, total notional).
2. "Flagged activity log" — every detected signal hit in the
   24 h window, grouped by market, descending by total
   notional. Table per market: wallet, side, size, price,
   signal tag(s), timestamp.
3. "Top 20 markets by flagged notional" — paired-down version of
   the current top table, with links.
4. Appendix: compact signal glossary (2 lines per signal,
   pointing at the SPEC docs).

**Scope:**

- Delete `scripts/templates/report.md.jinja2` sections 2/3/4.
- New generation path: `scripts/build-appendix-pdf.py` takes
  `alert_daily_rollup` + `wallet_profiles` + gamma metadata,
  renders a minimal markdown, converts via the existing
  direct-wkhtmltopdf path (tighter CSS is already in place).
- `scripts/newsletter-daily.py` attaches this new PDF instead of
  the legacy market-snapshot.

**Gate:**

- PDF ≤ 200 KB, ≤ 5 pages for typical day.
- M. advisor rubric pass on PDF.

**Est:** 1 session.

---

## Phase S4 — P1 signals

Adds P1-tier signals. Requires ≥ 7 days of `alert_daily_rollup`
data accumulated from Phase S1.

**Signals delivered:**

| # | Signal | Source doc | New dep |
|---|---|---|---|
| 01-D | Entity tag | 01-informed-flow.md § 01-D | entities.yaml curated |
| 01-E | Cross-market first-appearance | 01-informed-flow.md § 01-E | 7-day history |
| 02-B | VPIN | 02-microstructure.md § 02-B | Redis rolling-state |
| 02-D | Price-impact asymmetry | 02-microstructure.md § 02-D | /trades |
| 03-B | Taker/maker split | 03-volume-liquidity.md § 03-B | /trades orderType |
| 03-C | Book depth imbalance | 03-volume-liquidity.md § 03-C | CLOB /book + WS |
| 04-A | Directional break | 04-price-dynamics.md § 04-A | /trades |
| 04-D | Mean-reversion failure | 04-price-dynamics.md § 04-D | /trades |
| 05-A | Proximity acceleration | 05-event-catalyst.md § 05-A | /trades + gamma |

**Scope:** same pattern as S1 — one module per signal, plugged
into `detector/composer.py`.

**Gate:** S1 gates + each new signal has a scenario test.

---

## Phase S5 — Weekly ship

Build the weekly newsletter per SPEC-NEWSLETTERS-POLYMARKET § 5
against the Phase S4 signal set.

**Unlocks:**

- Wallet-of-the-week profile (requires 7 days of wallet history)
- Sniper-cluster detection over multi-day window
- Aged-callouts price-movement section (requires `price_at_flag`
  persisted — schema change needed; see
  IMPLEMENTATION-TODOS § 5.5)

**Gate:**

- Monday canary send; advisor rubric pass.

---

## Phase S6 — P2 signals + monthly ship

**Signals delivered:**

| # | Signal | Source doc | New dep |
|---|---|---|---|
| 01-C | Funding origin (bar + cluster) | 01-informed-flow.md § 01-C | **Tier 3 RPC** |
| 04-B | Divergence vs comparable | 04-price-dynamics.md § 04-B | comparable_markets.yaml |
| 04-C | Autocorrelation | 04-price-dynamics.md § 04-C | /trades |
| 05-B | Scheduled-event window | 05-event-catalyst.md § 05-B | scheduled_events.yaml |
| 06-A | Multi-outcome arithmetic | 06-cross-market.md § 06-A | gamma events |
| 06-B | Candidate/party containment | 06-cross-market.md § 06-B | containment_pairs.yaml |

**Gate:** S5 gates + monthly canary send; advisor rubric pass.

---

## Phase S7 — Tier-3 enabler + roadmap signals

Blocked on operator procurement of paid Polygon RPC. Once done:

**Signals delivered:**

- 01-A canonical variant (on-chain first-seen)
- 01-C funding-origin cluster graph (monthly feature)
- Tier-3 canonical on-chain indexer per
  IMPLEMENTATION-TODOS § 14.2.

**Roadmap deferrals:**

- 05-C news correlation (requires news feed vendor).
- 06-D cross-venue arbitrage (requires Kalshi / Manifold API
  integrations).
- 06-E correlated-markets break (requires P2 calibration first).

---

## 2. Cross-cutting requirements

### 2.1 Testing

Every signal module MUST ship with:

- A **unit test** per documented false-positive mode.
- A **scenario test** that replays a fixture capture containing
  at least one positive case.
- A **regression test** that asserts the 2024-election replay
  still flags the Fredi9999 cluster (gold-standard ground truth).

### 2.2 Calibration

- Every phase exit produces a `docs/signals/CALIBRATION-{date}.md`
  recording per-signal firing rate, overlap rate, and
  (once outcome labelling exists) precision / recall.
- `scripts/sanity-band-check.py` extends to cover each new
  signal.

### 2.3 Documentation

- Adding a signal updates the per-category doc AND the taxonomy
  table in `SPEC-MARKET-SIGNALS.md` § 3.
- Retiring a signal marks the taxonomy entry `RETIRED` + reason.
- Both are appended to SPEC-MARKET-SIGNALS audit log.

### 2.4 Reliability surfaces

- Every signal's `reliability_band` is rendered next to its
  tag in the newsletter body when the band is `low` or `medium`.
- `high` is reserved and only applied after a 30-day clean
  precision run.

### 2.5 Operator secrets inventory

- `entities.yaml` is repo-committed.
- `scheduled_events.yaml` is repo-committed.
- `comparable_markets.yaml` / `containment_pairs.yaml` /
  `correlated_pairs.yaml` — all repo-committed.
- Any paid-API keys (future news feed, Kalshi, RPC) live in
  `.env` + SPEC-NEWSLETTERS-POLYMARKET § 10.5 (never in repo).

---

## 3. Dependency matrix (at a glance)

```
                 S0  S1  S2  S3  S4  S5  S6  S7
data-api          *   *   *   *   *   *   *   *
gamma-api         *   *   *   *   *   *   *   *
alert_daily_rollup    *   *   *   *   *   *   *
entities.yaml                     *   *   *   *
scheduled_events.yaml                         *
Redis rolling-state                   *       *
CLOB /book + WS book-state         (*)(*)     *
Tier-3 RPC                                    *
```

---

## 4. Retirement tracker

Track in-field signals moved from active to retired:

| Signal | Retired | Reason | Audit |
|---|---|---|---|
| "near-certain" obs | 2026-04-20 | SPEC § 6 — not informational | SPEC-MARKET-SIGNALS audit log |
| "top liquidity" section | 2026-04-20 | SPEC § 6 — anti-signal | same |
| "recently created" section | 2026-04-20 | SPEC § 6 — 5-min bot spam | same |
| "thin book ratio alone" | 2026-04-20 | SPEC § 6 — gate only, not signal | same |

---

## 5. Audit log

- 2026-04-20 — initial plan drafted. Phases S0-S7 defined.
