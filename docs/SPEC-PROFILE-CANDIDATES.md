---
title: Profile Candidate Stats — Phase 2
status: Phase 2 (no pruning, no final-set commitment)
companion: docs/SPEC-PROFILE-LITERATURE.md
---

# SPEC-PROFILE-CANDIDATES — wallet + market profile: computable candidate pool

**Phase:** 2 of the profile-system plan (see `mellow-enchanting-melody.md`).
Phase 1 (`docs/SPEC-PROFILE-LITERATURE.md`) enumerated 27 cited
dimensions across three buckets. This document enumerates every
wallet- and market-level stat we could *actually compute today*
from data already in hand, citing back to Phase-1 entries by section
number. No stat is locked in; no acronym is proposed; no empirical
pruning has happened — that is Phase 3's job.

---

## 1. Intent

One goal: produce a rigorous, computation-ready catalogue for
Phase-3 empirical diagnostics. Each candidate is defined at the
level a quant can re-derive — formula, data inputs, expected range,
published name, redundancy vs. sibling candidates. Nothing is
selected, nothing is excluded except where a stat requires data we
do not yet capture (recorded in the audit log for a future
iteration).

Bounds per the plan (§ "Phase 2: candidate stat pool"):

- 15 ≤ wallet candidates ≤ 20
- 10 ≤ market candidates ≤ 12

We land at **18 wallet** and **12 market** candidates. Seven
wallet-level and two market-level items were considered and dropped
at this stage — they are listed in § 5, the audit log, with the
reason they did not make the Phase-2 pool.

---

## 2. Inputs we already have

The "data already on hand" constraint is load-bearing. Every stat
below uses only fields that exist in one of the four surfaces
catalogued here — no new pipelines are introduced in Phase 2.

### 2.1 Polymarket `data-api.polymarket.com/trades` (poll every 30 s)

Implemented in `src/polymarket_insider_tracker/ingestor/data_api.py`
and parsed into `TradeEvent` (`ingestor/models.py`). Fields per row
(confirmed by `_trade_from_api_row` and by the WS-parser sibling
`TradeEvent.from_websocket_message`):

| Field | Type | Notes |
|---|---|---|
| `transactionHash` | str | Unique trade id, dedupe key |
| `proxyWallet` | str (hex) | Trader wallet address (lowercased everywhere downstream) |
| `conditionId` | str | Market / CTF condition id |
| `asset` | str | ERC-1155 token id (per-outcome) |
| `side` | "BUY" \| "SELL" | Normalised upstream |
| `outcome` | str | Human outcome label (e.g. "Yes") |
| `outcomeIndex` | int | 0 or 1 |
| `price` | decimal | Fill price, [0, 1] |
| `size` | decimal | Shares filled |
| `timestamp` | unix-seconds int | Trade time |
| `slug`, `eventSlug`, `title` | str | Market / event identifiers |
| `name`, `pseudonym` | str | Trader display names (self-reported) |

**Derived:** `notional = price × size`; signed flow via `side`.
**Not captured:** inside-quote bid/ask at trade time (we only have
the fill price, not the contemporaneous best-bid / best-ask).

### 2.2 Polymarket `gamma-api` market metadata

Cached via `src/polymarket_insider_tracker/ingestor/metadata_sync.py`
into Redis, exposed to signals as `SignalContext.market_meta`
(lowercased `conditionId` → dict). Fields used by current signals
and `detector/signals/gates.py`:

| Field | Used by | Notes |
|---|---|---|
| `question`, `slug`, `category` | all | Title + url building |
| `lastTradePrice`, `bestBid`, `bestAsk` | gates, profile | Implied probability & spread |
| `volume24hr` | velocity, gates | Trailing-24 h dollar volume |
| `volumeNum` | velocity | All-time dollar volume |
| `liquidityClob` | thin-book gate | Posted CLOB liquidity (depth proxy) |
| `startDate`, `startDateIso`, `endDate`, `endDateIso` | gates, velocity | Market lifespan |
| `active`, `closed` | pipeline | Lifecycle flags |

### 2.3 Signal-output JSON (`SignalHit.row` dicts + `DailyReport`)

Produced by `detector/composer.py` and persisted under
`artifacts/newsletter-*/…` by `scripts/newsletter-daily.py`. Each
row carries the already-computed fields from the six P0 signals
(`01-A-fresh-wallet`, `01-B-unusual-size`, `02-A-order-flow-imbalance`,
`02-C-stealth-cluster`, `03-A-volume-velocity`, plus the `03-D`
thin-book gate): per-hit score, per-market trade-count, buy /
sell notional, imbalance fraction, p90 single-trade size, fresh-
wallet first-seen timestamp, wallet-level contributor breakdowns.

### 2.4 Cross-wallet profiler artefacts (`profiler/`)

`src/polymarket_insider_tracker/profiler/` holds the existing
sniper-cluster detector (`profiler/analyzer.py` + `entities.py`),
funding-graph scaffolding (`profiler/funding_graph.py`), and the
`WalletProfile` Pydantic model (`profiler/models.py`). The
`first_seen_ts` probe from `detector/signals/fresh_wallet.py`
already gives us a persistent per-wallet "Polymarket-first-trade"
timestamp — the only wallet-tenure input Phase 2 requires.

### 2.5 Out-of-scope data (flagged in audit log)

- **Polygon chain indexer** — wallet ↔ wallet transfer graphs,
  EOA age, mint/burn/merge/split legs. The Tsang-Yang (2026)
  decomposition (§ 5.3 of Phase 1) depends on distinguishing
  exchange trades from mint/burn legs; `/trades` alone can't do
  that, so candidates that need it are deferred.
- **Market resolution outcomes** — any realised-PnL / win-rate
  stat. Gated on Tier-3, per plan § "Out of scope".
- **Orderbook snapshots at trade time** — we store live book via
  `ingestor/clob_client.py` but do not persist per-trade snapshots,
  so spread-decomposition stats that need `(trade, contemporaneous
  midquote)` pairs are out.

---

## 3. Wallet candidates

Eighteen candidates. Each names a Phase-1 literature entry by
section (e.g. "Phase 1 § 3.5" = Barclay-Warner). Redundancy notes
flag pairs Phase-3 diagnostics should correlation-check; the `|r|
> 0.85` drop rule comes from plan § "Phase 3 empirical pruning".

### W1. Tenure in days (Polymarket-first-trade)

- **Name (as published):** Wallet age / tenure — Wolfers & Zitzewitz
  (2004) uses it as a participant-heterogeneity axis (Phase 1
  § 5.1); Zerion, DeBank expose it numerically (Phase 1 § 4).
- **Literature anchor:** Phase 1 § 5.1 (Wolfers-Zitzewitz) +
  Phase 1 § 4 (Zerion / DeBank rows).
- **Formula:**
  `tenure_days = (now − first_trade_ts) / 86400`
  where `first_trade_ts = min(trade.timestamp for trade in
  data_api_trades(user=wallet))`.
- **Inputs:** `/trades?user=<wallet>&limit=1000` (already probed
  by `detector/signals/fresh_wallet.py::_fetch_first_trade_timestamps`).
- **Range:** `[0, ∞)` days; Polymarket launched 2020, so
  empirically `[0, ~1800]`. Long right tail.
- **Redundancy:** none with other wallet stats, but tightly
  coupled to the Phase-1 "fresh wallet" heuristic — drop neither;
  one is the raw, one is a thresholded flag.

### W2. Gross notional, rolling window

- **Name (as published):** Dollar volume / gross turnover in
  notional terms — baseline input to Fama-MacBeth (1973) turnover
  and Amihud (2002) ILLIQ.
- **Literature anchor:** Phase 1 § 3.2 (input to Amihud), Phase 1
  § 4 (Arkham, Zerion expose as "volume").
- **Formula:**
  `gross_notional_w = Σ_{t ∈ window} price_t · size_t`
  over the configured rolling window (default 30 d, per plan
  Phase-4 YAML stub).
- **Inputs:** `TradeEvent.price`, `TradeEvent.size`,
  `TradeEvent.timestamp`; filtered to `wallet_address == target`.
- **Range:** `[0, ∞)` USD; empirical distribution on prediction
  markets is extremely fat-tailed (log-normal to power-law). Use
  `log10(1 + gross)` for clustering.
- **Redundancy:** overlaps with W6 (trade count) at `|r| ≈ 0.6-0.8`
  empirically on equities (Lo-Wang 2000); drop the less-interpretable
  if they correlate > 0.85 here.

### W3. Net notional (signed buy − sell)

- **Name (as published):** Signed order flow — Hasbrouck (1991)
  signed-trade indicator; directly mirrors the market-level
  OFI aggregator in `detector/signals/order_flow_imbalance.py`
  applied per-wallet.
- **Literature anchor:** Phase 1 § 3.6 (Glosten-Milgrom
  adverse-selection variant: signed direction is the decomposition
  axis) + Phase 1 § 3.7 (Hasbrouck information share); operationally,
  the current OFI signal already aggregates this at market level.
- **Formula:**
  `net_notional_w = Σ_{t ∈ window} sign(t) · price_t · size_t`
  where `sign(t) = +1 if side = BUY else −1`.
- **Inputs:** same as W2 plus `side`.
- **Range:** `(−∞, +∞)` USD; symmetric about zero for uninformed
  flow, skewed for directional traders.
- **Redundancy:** `|net|` correlates with W2; report both raw net
  and the ratio `net / gross` as two distinct stats (see W14).

### W4. Capital-at-risk (median outstanding position notional)

- **Name (as published):** Position size / capital at risk —
  DeBank "net worth" and Arkham "entity-level P&L from cost basis"
  are the in-production analogues (Phase 1 § 4).
- **Literature anchor:** Phase 1 § 4 (Arkham "entity-level P&L")
  + Phase 1 § 3.11 (HHI is computed *over* position sizes; this
  is the per-wallet magnitude input).
- **Formula:** operationalised as the median of running
  per-(wallet, conditionId, outcomeIndex) signed-share inventory
  at each trade's timestamp:
  `inv_t(w, m, o) = Σ_{t' ≤ t, t'.wallet=w, t'.market=m, t'.outcome=o}
      sign(t') · size_{t'}`
  `CaR_w = median_{t ∈ window} Σ_{m, o} |inv_t(w,m,o)| · price_{last
  trade (m,o) ≤ t}`.
- **Inputs:** `TradeEvent.{size, price, side, conditionId,
  outcomeIndex, timestamp}`; per-market last-price series (already
  built implicitly by the composer's trade-scan).
- **Range:** `[0, ∞)` USD; right-skewed.
- **Redundancy:** with W2 via `|r|` likely 0.5-0.7; kept because
  W2 captures flow and W4 captures exposure — different axes.

### W5. Turnover rate

- **Name (as published):** Turnover — the definitional ratio; not
  in the 1973 Fama-MacBeth paper (flagged explicitly in Phase 1
  § 3 "Dropped from the starter list") but a canonical profiler
  dimension.
- **Literature anchor:** Phase 1 § 3 dropped-list note; usage
  documented in Lo & Wang (2000) "Trading Volume: Definitions,
  Data Analysis, and Implications of Portfolio Theory,"
  *Review of Financial Studies* 13(2): 257-300.
- **Formula:** `turnover_w = gross_notional_w / CaR_w` (W2 / W4).
- **Inputs:** W2 + W4.
- **Range:** `[0, ∞)`; low for buy-and-hold, high for rapid
  rotation. Right-skewed.
- **Redundancy:** mathematically a ratio of W2 and W4 — will
  correlate with both at `|r| ≈ 0.4-0.7`; Phase 3 should keep
  turnover unless both components survive independently.

### W6. Trade count

- **Name (as published):** Activity rate / number of trades —
  a definitional input to PIN (§ 3.3) and to the Barclay-Warner
  medium-size share (§ 3.5).
- **Literature anchor:** Phase 1 § 3.3 (PIN, `N_buy + N_sell`)
  + Phase 1 § 3.5 (Barclay-Warner per-size-bucket trade counts).
- **Formula:** `trades_w = |{t ∈ window : t.wallet = w}|`.
- **Inputs:** `TradeEvent.wallet_address`, `.timestamp`.
- **Range:** non-negative int; skewed with a mode at 1.
- **Redundancy:** likely `|r| > 0.8` with W2 on frequent traders;
  keep both through Phase 3 — one captures intensity, the other
  aggregate size.

### W7. Kyle's λ contribution share — wallet participation in price impact

- **Name (as published):** Kyle's λ, wallet-level decomposition
  — Kyle (1985).
- **Literature anchor:** Phase 1 § 3.1.
- **Formula:** per market fit `ΔP_t = λ_m · q_t + ε_t` where
  `q_t` is the signed-notional of trade `t` and `ΔP_t = price_t −
  price_{t−1}` within that market. Then
  `share_w,m = (Σ_{t ∈ window, t.wallet=w, t.market=m} λ_m · q_t)
                / (Σ_{t ∈ window, t.market=m} λ_m · |q_t|)`
  Aggregate across markets weighted by per-market gross notional:
  `wallet_share_w = Σ_m share_w,m · gross_w,m / Σ_m gross_w,m`.
- **Inputs:** `TradeEvent.{price, size, side, conditionId,
  timestamp}`; within-market per-trade price deltas.
- **Range:** `[0, 1]`; expected mode near 0, long right tail.
- **Redundancy:** with W3 via signed flow magnitude (`|r|` likely
  0.3-0.5); distinct because λ-share is impact-weighted and W3 is
  raw notional.

### W8. Barclay-Warner stealth index (wallet variant)

- **Name (as published):** Stealth-trading medium-size share —
  Barclay & Warner (1993), wallet recast per Phase 1 § 3.5 ("can
  be recast as a wallet-level stat").
- **Literature anchor:** Phase 1 § 3.5.
- **Formula:** Let `S_m` be the per-market trade-size distribution;
  define `medium_m = [p25(S_m), p75(S_m)]`. Then
  `stealth_w = (Σ_{t ∈ window, t.wallet=w, size·price ∈ medium_{t.market}}
                |size·price|)
               / (Σ_{t ∈ window, t.wallet=w} |size·price|)`.
- **Inputs:** `TradeEvent.{size, price, conditionId,
  wallet_address}`; per-market size-bucket quantiles (derived
  on the fly from the window's trades).
- **Range:** `[0, 1]`; uniform-ish by construction if the wallet
  mirrors base rates, skewed toward 1 for stealth traders.
- **Redundancy:** should correlate with the Variant-B hit count
  from `detector/signals/unusual_size.py` (same Barclay-Warner
  pattern); if `|r| > 0.85`, drop this raw stat and keep the
  existing stealth-cluster hit count.

### W9. Turnover-adjusted Hasbrouck lead-lag

- **Name (as published):** Hasbrouck information share (wallet
  lead-lag variant, per Phase 1 § 3.7 mapping).
- **Literature anchor:** Phase 1 § 3.7.
- **Formula:** for each of the wallet's `(market, timestamp)`
  entries, compute
  `lead_Δp_w,m,t = P_{m, t + Δ} − P_{m, t}` for fixed Δ = 5 min.
  Then
  `lead_w = median_{entries} lead_Δp_w,m,t · sign(trade_w,m,t)`.
  A positive median means the wallet's entries precede
  same-direction price moves.
- **Inputs:** `TradeEvent.{price, side, conditionId, timestamp,
  wallet_address}`; same-market post-trade price series (built by
  scanning the window).
- **Range:** `(−0.5, +0.5)` price units; near zero for noise
  traders, positive for leaders, negative for laggards /
  counter-flow.
- **Redundancy:** overlaps in spirit with W7 but Kyle measures
  simultaneous impact while Hasbrouck measures predictive lead —
  keep both; `|r|` expected < 0.5.

### W10. Market breadth — Herfindahl across markets touched

- **Name (as published):** Herfindahl-Hirschman concentration,
  per-wallet breadth variant — Hirschman (1945).
- **Literature anchor:** Phase 1 § 3.11; Phase 1 § 4 (DeBank
  portfolio-allocation analogue).
- **Formula:** let `s_{w,m} = gross_{w,m} / Σ_{m'} gross_{w,m'}`
  over markets touched by wallet `w` in the window; then
  `HHI_markets_w = Σ_m s_{w,m}²`.
- **Inputs:** `TradeEvent.{conditionId, size, price,
  wallet_address}`.
- **Range:** `[1/N, 1]` where `N` is markets touched; `1` = all
  volume on a single market, `1/N` = uniform allocation.
- **Redundancy:** complement of a "distinct-markets-count"
  scalar; the latter is more readable but less info-dense. Keep
  HHI, drop count if `|r| > 0.85`.

### W11. Category breadth — Herfindahl across categories

- **Name (as published):** HHI applied to category allocation.
- **Literature anchor:** Phase 1 § 3.11 + Phase 1 § 4 (Nansen
  "Sector Specialist" label operationally equivalent).
- **Formula:** same as W10 but grouped by `market_meta[m].category`
  (the gamma-api `category` or derived via `derive_category()` in
  `ingestor/models.py`).
- **Inputs:** W10 inputs + `MarketMetadata.category`.
- **Range:** `[1/K, 1]`, `K ≈ 7` (the defined category set).
- **Redundancy:** will correlate with W10 at `|r| ≈ 0.4-0.6` for
  most wallets; expected to diverge for wallets that specialise
  in *many* markets *within one category* (where W10 is low but
  W11 is high).

### W12. Trade-size skew — Cong-Li-Tang-Yang wash-trade forensics

- **Name (as published):** Trade-size distribution forensics —
  Cong, Li, Tang & Yang (2023).
- **Literature anchor:** Phase 1 § 3.13.
- **Formula:** compute two scalars over the wallet's per-trade
  notional distribution `X_w = {price · size for t ∈ window,
  t.wallet = w}`:
  - `benford_χ²_w = Σ_{d=1..9} (freq_d − benford_d)² / benford_d`
    where `freq_d` is the first-significant-digit frequency of
    `X_w` and `benford_d = log₁₀(1 + 1/d)`.
  - `round_share_w = |{x ∈ X_w : x mod 100 = 0}| / |X_w|`.
  Report as a two-tuple; Phase 3 may collapse to one.
- **Inputs:** `TradeEvent.{price, size, wallet_address}`.
- **Range:** χ² `[0, ∞)`, right-skewed; round-share `[0, 1]`.
- **Redundancy:** the two sub-stats are within-family; neither
  overlaps with volume / flow stats.

### W13. Co-timing-cluster participation rate

- **Name (as published):** Co-timed cluster membership — the
  signal-side operationalisation of the Easley-López de Prado
  toxicity measure (Phase 1 § 3.4) and FINRA SMARTS "coordinated
  activity" alert family (Phase 1 § 4).
- **Literature anchor:** Phase 1 § 3.4 + § 4 (FINRA row).
- **Formula:** read the `02-C-stealth-cluster` hits for the
  window; for wallet `w`,
  `cluster_rate_w = |{hit : w ∈ hit.top_wallets}|
                     / max(1, |{markets touched by w}|)`.
- **Inputs:** signal-output JSON (`SignalHit.row["top_wallets"]`
  from `stealth_cluster.py`).
- **Range:** `[0, 1]`; near zero for lone traders, elevated for
  coordination candidates.
- **Redundancy:** with W14 (directional concentration) at `|r|`
  likely < 0.4.

### W14. Directional concentration `|net| / gross`

- **Name (as published):** One-sided-ratio — Page & Siemroth (2020)
  use it as the raw empirical predictor in their PIN
  decomposition; mirrors the market-level OFI signal's
  `imbalance` metric applied per-wallet.
- **Literature anchor:** Phase 1 § 5.2.
- **Formula:**
  `dir_conc_w = |net_notional_w| / gross_notional_w`
  = `|W3| / W2`.
- **Inputs:** W2 + W3.
- **Range:** `[0, 1]`; 0 = perfectly balanced, 1 = all one-sided.
- **Redundancy:** with W3 as a ratio of it; keep through Phase 3
  as the normalised form.

### W15. Large-trade imbalance — Ng-Peng-Tao-Zhou wallet variant

- **Name (as published):** Large-trade net-order-imbalance — Ng,
  Peng, Tao & Zhou (2025).
- **Literature anchor:** Phase 1 § 5.4.
- **Formula:** let `large_w = {t ∈ window, t.wallet = w,
  |price·size| ≥ p90(all-window notionals)}`; then
  `large_imb_w = Σ_{t ∈ large_w} sign(t) · price·size /
                  Σ_{t ∈ large_w} price·size`.
- **Inputs:** `TradeEvent.{side, price, size, wallet_address,
  timestamp}`; p90 cutoff already computed per-market inside
  `detector/signals/unusual_size.py` and available as
  `SignalHit.row["notional"]` for the top-bucket samples.
- **Range:** `[−1, +1]`.
- **Redundancy:** with W3 via large-trade dominance on whales
  (`|r|` likely 0.5-0.7); distinct because W3 uses all trades and
  W15 uses only the p90+ tail.

### W16. Fresh-wallet scoring contribution

- **Name (as published):** Fresh-wallet flag — in-production
  heuristic (Polywhaler, Polymarket Alerts; Phase 1 § 4 last row).
- **Literature anchor:** Phase 1 § 4 (community profilers).
- **Formula:** read the `01-A-fresh-wallet` signal's
  `first_seen_ts` (already cached per-wallet in
  `detector/signals/fresh_wallet.py::_wallet_cache`) and combine
  with trade-size:
  `fresh_score_w = (1 if tenure_days < 30 else 0)
                    · min(1, biggest_trade_w / $10 000)`.
- **Inputs:** W1 + per-wallet max single-trade notional (derivable
  from `TradeEvent.price, size`).
- **Range:** `[0, 1]`.
- **Redundancy:** functionally dependent on W1; Phase 3 should
  drop one if `|r| > 0.85` — likely keep W1 (the continuous form).

### W17. Per-market max-payoff concentration

- **Name (as published):** Share-count-weighted max-payoff — the
  `fresh_wallet.py` signal already surfaces `max_payoff`
  (total_shares · $1 on a BUY, total cash received on a SELL);
  the distribution-profile variant is a Nansen-style "all-in
  concentration" (Phase 1 § 4, community profilers row).
- **Literature anchor:** Phase 1 § 4 (Polymarket-community
  profilers — "all-in concentration on a single market").
- **Formula:** per wallet,
  `max_payoff_w = max_m total_shares_{w,m}`
  `top_market_share_w = max_payoff_w / Σ_m total_shares_{w,m}`.
  Report as a two-tuple; Phase 3 may collapse.
- **Inputs:** `TradeEvent.{size, side, conditionId,
  wallet_address}`.
- **Range:** shares `[0, ∞)`; share `[0, 1]`.
- **Redundancy:** share-component correlates with W10 (`|r|`
  likely 0.6-0.8) — both measure single-market concentration;
  drop one if above 0.85.

### W18. Pseudonym-set cardinality

- **Name (as published):** Not directly cited; closest analogue
  is Arkham's "entity grouping — many addresses → one entity"
  (Phase 1 § 4). **(not in literature — original, flagged.)**
  Justification for inclusion: `/trades` ships the self-reported
  `name` and `pseudonym` fields, so the stat is free to compute
  and is informative for Nansen-style labelling. Treat as a
  candidate and let Phase 3 diagnostics decide whether it adds
  separation.
- **Literature anchor:** Phase 1 § 4 (Arkham entity-grouping row).
- **Formula:**
  `pseudonym_count_w = |{pseudonym(t) : t ∈ window, t.wallet = w}
                         \ {""}}|`.
- **Inputs:** `TradeEvent.{wallet_address, pseudonym}`.
- **Range:** non-negative int; mode at 0 (no pseudonym) or 1
  (one self-label).
- **Redundancy:** none expected; distinct behavioural dimension.

---

## 4. Market candidates

Twelve candidates, at the upper plan bound. Every stat here is
market-level (`conditionId` is the keying dimension). Phase 3
prunes strictly to 4-5 per `plan §"Decisions locked"`.

### M1. Amihud illiquidity `ILLIQ`

- **Name (as published):** Amihud illiquidity — Amihud (2002).
- **Literature anchor:** Phase 1 § 3.2.
- **Formula:** for market `m`, window split into daily buckets `d`:
  `ILLIQ_m = mean_d (|R_{m,d}| / V_{m,d})`
  where `R_{m,d} = (P_{m,d,close} − P_{m,d,open}) / P_{m,d,open}`
  and `V_{m,d}` is the day's dollar volume.
- **Inputs:** per-day open/close derivable from `TradeEvent.price
  + timestamp`; per-day volume derivable from `size × price`
  aggregation. `gamma-api.volume24hr` provides the single-day
  form directly for the most recent day.
- **Range:** `[0, ∞)` per-dollar; highly right-skewed, log-normal
  in empirical equity samples.
- **Redundancy:** with M2 (Kyle's λ) at `|r|` typically 0.6-0.8 —
  both measure price-impact; keep if they diverge on Polymarket's
  bounded price support.

### M2. Kyle's λ

- **Name (as published):** Kyle's λ — Kyle (1985).
- **Literature anchor:** Phase 1 § 3.1.
- **Formula:** per market `m`,
  `λ_m = Cov(ΔP_t, q_t) / Var(q_t)`
  over consecutive within-market trades `t`, with `q_t =
  sign(t) · price_t · size_t` and `ΔP_t = price_t − price_{t−1}`.
- **Inputs:** `TradeEvent.{price, size, side, conditionId,
  timestamp}`.
- **Range:** `(0, ∞)` (should be positive for well-behaved
  markets); log-scale for display.
- **Redundancy:** with M1; both are price-impact measures.

### M3. VPIN — volume-synchronised probability of informed trading

- **Name (as published):** VPIN — Easley, López de Prado & O'Hara
  (2012). Plan § "Phase 2 starter list" labelled this "existing
  P1 signal" but it is not in the current `REGISTRY` (as of this
  branch). Reworded to reflect state: VPIN is computable but not
  yet a live signal.
- **Literature anchor:** Phase 1 § 3.4.
- **Formula:** partition the market's window trades into `N`
  buckets each of equal volume `V_bucket`; in each bucket, use
  bulk-volume classification (BVC): `V_buy_i = V_bucket ·
  Φ((ΔP_i / σ))` where `σ` is the bucket-level price-change std
  and `Φ` is the standard-normal CDF; `V_sell_i = V_bucket −
  V_buy_i`. Then
  `VPIN_m = (1/N) · Σ_i |V_buy_i − V_sell_i| / V_bucket`.
- **Inputs:** `TradeEvent.{price, size, conditionId, timestamp}`.
- **Range:** `[0, 1]`; > 0.4 flagged "toxic" in the original
  paper.
- **Redundancy:** with the existing `02-A-order-flow-imbalance`
  signal's `imbalance` fraction at `|r| ≈ 0.7-0.9`; decide in
  Phase 3.

### M4. Realised volatility

- **Name (as published):** Realised volatility — Andersen,
  Bollerslev, Diebold & Labys (2001).
- **Literature anchor:** Phase 1 § 3.10.
- **Formula:** for market `m` over window `W`,
  `RV_m = √ Σ_{t ∈ W, consecutive} (P_t − P_{t−1})²`
  at the native trade-by-trade sampling.
- **Inputs:** `TradeEvent.{price, conditionId, timestamp}`.
- **Range:** `[0, ~0.5)` on `[0, 1]`-priced markets; right-skewed.
- **Redundancy:** with M5 and M6 (both spread estimators derive
  from second moments of `ΔP`); `|r|` likely 0.5-0.7.

### M5. Roll's implied spread

- **Name (as published):** Roll's implied effective spread —
  Roll (1984).
- **Literature anchor:** Phase 1 § 3.8.
- **Formula:**
  `spread_Roll_m = 2 · √ max(0, −Cov(ΔP_t, ΔP_{t−1}))`
  on consecutive within-market trades.
- **Inputs:** `TradeEvent.{price, conditionId, timestamp}`.
- **Range:** `[0, ~0.2]` on `[0, 1]`-priced markets; undefined
  when serial covariance is positive — convention is to report 0.
- **Redundancy:** with M4 via shared use of `ΔP`; with M6 as a
  sibling spread estimator — keep one in Phase 3.

### M6. Corwin-Schultz high-low spread

- **Name (as published):** Corwin & Schultz (2012) high-low
  estimator.
- **Literature anchor:** Phase 1 § 3.9.
- **Formula:** with per-day `H_{m,d} = max_{t ∈ d} P_t` and
  `L_{m,d} = min_{t ∈ d} P_t`:
  `β_d = ln²(H_d/L_d) + ln²(H_{d+1}/L_{d+1})`,
  `γ_d = ln²(max(H_d, H_{d+1}) / min(L_d, L_{d+1}))`,
  `α_d = (√(2β_d) − √β_d) / (3 − 2√2) − √(γ_d / (3 − 2√2))`,
  `spread_CS_m = mean_d 2·(e^{α_d} − 1) / (1 + e^{α_d})`.
- **Inputs:** `TradeEvent.{price, conditionId, timestamp}`; daily
  H/L derivable trivially.
- **Range:** `[0, ~0.2]`.
- **Redundancy:** with M5.

### M7. Volume velocity — 24 h vs. all-time daily baseline

- **Name (as published):** Velocity ratio — current production
  signal `03-A-volume-velocity` (Phase 1 § 4, Polymarket-ecosystem
  profilers row).
- **Literature anchor:** Phase 1 § 4 (community-profilers).
- **Formula:**
  `velocity_m = volume24hr_m / (volumeNum_m / days_active_m)`
  where `days_active_m = (now − startDate_m) / 86400`.
- **Inputs:** `gamma-api.{volume24hr, volumeNum, startDate,
  startDateIso}`.
- **Range:** `[0, ∞)`; `1` = on baseline, `> 3` is the current
  signal threshold.
- **Redundancy:** with the fired-signal boolean; keep the
  continuous form.

### M8. Participant count per $1M volume

- **Name (as published):** Participant diversity — reported by
  Tsang & Yang (2026) as a structural-profile axis (Phase 1
  § 5.3).
- **Literature anchor:** Phase 1 § 5.3.
- **Formula:**
  `participants_per_M_m = |{wallet : ∃ t, t.wallet=w ∧ t.market=m,
                            t ∈ window}| / (gross_volume_m / 10^6)`.
- **Inputs:** `TradeEvent.{wallet_address, conditionId, size,
  price}`.
- **Range:** `[0, ~10³]`; empirically fat-tailed — markets with
  few whales vs. many retail sit at opposite extremes.
- **Redundancy:** with M9 (HHI-of-participants) as its inverse-
  shape — `|r|` typically −0.5 to −0.8; keep both as they encode
  the distribution differently.

### M9. Participant HHI — Herfindahl of wallet shares

- **Name (as published):** HHI of participant volume shares —
  Hirschman (1945), per Phase 1 § 3.11 market-level variant.
- **Literature anchor:** Phase 1 § 3.11.
- **Formula:** let `s_{w,m} = gross_{w,m} / Σ_{w'} gross_{w',m}`;
  `HHI_m = Σ_w s_{w,m}²`.
- **Inputs:** `TradeEvent.{wallet_address, conditionId, size,
  price}`.
- **Range:** `[1/N, 1]`; near zero for broad markets, near one
  for whale-dominated.
- **Redundancy:** with M8.

### M10. Time to resolution

- **Name (as published):** Horizon / time-to-resolution —
  Wolfers-Zitzewitz (2004), Phase 1 § 5.1.
- **Literature anchor:** Phase 1 § 5.1.
- **Formula:**
  `hours_to_close_m = (endDate_m − now) / 3600`.
- **Inputs:** `gamma-api.endDate / endDateIso`.
- **Range:** `(0, ∞)` hours; near-zero just before resolution.
  Heavy left-tail near resolution (sports, weekly metrics).
- **Redundancy:** none with flow stats; composition axis for the
  gate logic.

### M11. Co-timed-cluster fired count

- **Name (as published):** Cluster firing frequency — Page &
  Siemroth (2020) "near-resolution timing concentration"
  proxy (Phase 1 § 5.2); also the FINRA SMARTS "coordinated
  activity" alert family (Phase 1 § 4).
- **Literature anchor:** Phase 1 § 5.2 + § 4 (FINRA).
- **Formula:** count `02-C-stealth-cluster` hits on market `m`
  during the window; equivalently
  `cluster_fires_m = |{hit ∈ signal_output_json :
                       hit.signal_id = "02-C-stealth-cluster" ∧
                       hit.market_id = m}|`.
- **Inputs:** signal-output JSON.
- **Range:** non-negative int; mode at 0, fat right tail on
  salient markets.
- **Redundancy:** with M9 (concentrated participation increases
  cluster likelihood) at `|r|` likely 0.4-0.6.

### M12. Implied-probability distance from extremes

- **Name (as published):** Extreme-price-avoidance metric —
  operationalisation of the Wolfers-Zitzewitz (2004)
  binary-market-calibration dimension (Phase 1 § 5.1) and of the
  gate already in `detector/signals/gates.py::price_in_band`.
- **Literature anchor:** Phase 1 § 5.1.
- **Formula:** `extreme_dist_m = 0.5 − |lastTradePrice_m − 0.5|`.
  Ranges in `[0, 0.5]`; `0` = at an extreme, `0.5` = at midpoint.
- **Inputs:** `gamma-api.lastTradePrice` (fallback:
  `(bestBid + bestAsk) / 2`).
- **Range:** `[0, 0.5]`.
- **Redundancy:** none with flow stats; distinct from M4-M6
  (volatility / spreads) which measure dispersion rather than
  level.

---

## 5. Audit log — considered and rejected before the pool

Seven wallet-level and two market-level candidates were considered
and excluded at this stage. All are "needs data we don't yet
capture," not "empirically weak" — Phase 3 is the right place to
drop stats on empirical grounds.

### Wallet

- **Realised PnL / ROI / win-rate** — Nansen Smart-DEX-Trader
  tiers (Phase 1 § 4), Reichenbach-Walther (Phase 1 § 5.5),
  Zerion / Arkham (Phase 1 § 4). Requires market-resolution
  outcomes; gated on Tier-3 chain indexer + resolution pipeline
  per plan § "Out of scope".
- **Disposition-effect ratio** (Shefrin-Statman; Phase 1 § 3.14).
  Requires per-wallet opened/closed positions with entry and
  exit prices — specifically the realised / paper gain/loss
  decomposition — which needs resolution outcomes.
- **Counterparty-set cardinality** (Chainalysis / TRM indirect
  exposure; Phase 1 § 4). Requires Polygon chain indexer to see
  address ↔ address transfer graph; listed as placeholder in the
  plan's starter row 16.
- **Funding-origin tag** (Polymarket-ecosystem profilers;
  Phase 1 § 4, community-profilers row). The `profiler/funding.py`
  scaffolding exists but is not populated without the chain
  indexer.
- **EOA age (first-on-chain timestamp, not first-Polymarket-trade)**
  — the cleaner Wolfers-Zitzewitz (Phase 1 § 5.1) tenure
  primitive. W1 is the Polymarket-first-trade surrogate; the
  true EOA-age needs Polygon RPC.
- **Cross-venue lead-lag** against Kalshi / PredictIt / Robinhood
  — Ng-Peng-Tao-Zhou (Phase 1 § 5.4). Requires matched-contract
  data from those venues; plan § "Out of scope" (P3 roadmap).
- **Mint/burn-adjusted exchange volume** — Tsang-Yang (Phase 1
  § 5.3) decomposition into exchange-equivalent, net-inflow,
  gross-activity. Distinguishing legs requires the Polygon chain
  indexer.

### Market

- **PIN** (Easley-Kiefer-O'Hara-Paperman; Phase 1 § 3.3). The
  structural EKOP model needs *daily* buy-initiated and sell-
  initiated trade *counts*, and the max-likelihood fit is
  unstable on short Polymarket samples. VPIN (M3) is the
  practical substitute and is already in the pool. Revisit once
  we have ≥ 30 days of trade history per market.
- **Adverse-selection spread component** (Glosten-Milgrom /
  Huang-Stoll; Phase 1 § 3.6). Needs contemporaneous best-bid /
  best-ask at each trade's timestamp, which we do not persist
  per-trade — `clob_client.py` pulls live books but doesn't store
  them alongside `TradeEvent`. Revisit when orderbook-snapshot
  persistence lands.

These nine drops are not a pruning list — they are "not even
worth computing yet" cases. Phase 3 will produce a separate
empirical-pruning audit from the 18 + 12 candidates above.

---

## 6. Handoff to Phase 3

Phase 3 (plan § "Phase 3: empirical pruning") computes all 30
candidates across the top ~1000 wallets and top ~200 markets of
our captured data, then runs three diagnostics: distribution
non-flatness, pairwise correlation (drop at `|r| > 0.85`), and
clustering-separation (Silhouette). Expected survivors per plan
§ "Decisions locked": **4-5 wallet stats, 4-5 market stats**.
This document's job is to make those diagnostics a purely
mechanical read from already-in-hand data. Nothing below the
four-per-side survivor count is committed yet.
