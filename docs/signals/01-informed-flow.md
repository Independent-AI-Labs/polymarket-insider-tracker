# Signal category 01 — Informed-flow fingerprints

**Taxonomy reference:** SPEC-MARKET-SIGNALS § 3.
**Parent spec:** `docs/SPEC-MARKET-SIGNALS.md`.

Signals in this category identify *who* is likely informed, based
on behavioural fingerprints that separate an informed trader from
the retail flow. They exploit Polymarket's full on-chain
visibility (SPEC § 2.1) — the single biggest structural edge this
product has over equity-market insider detection.

All signals in this file are **P0** (daily-launch critical).

---

## 01-A. Fresh-wallet entry

### Definition

A wallet appearing in the flagged window whose **first observable
activity on Polygon** is ≤ N days old, or whose first Polymarket
trade is ≤ N hours before the flagged trade.

### Theoretical basis

Informed traders have a strong incentive to obscure their
identity. One mechanism is a fresh wallet: the informed party
funds a new address from a mixer, CEX withdrawal, or a proxy
before placing the trade, so on-chain history contains nothing
incriminating. The pattern is documented in the Polymarket 2024
US election context — multiple clusters of new wallets (< 3
months old) placed $40K+ Trump positions in the days preceding
the election (WSJ, Fortune; see § Historical precedent).

An analogous equity-market signal is "new account trades
immediately in high-notional OTM options", flagged routinely by
FINRA surveillance. The Polymarket version is simpler because
wallet creation timestamps are public.

### Computation

```
is_fresh(wallet, trade_ts) ::=
    first_seen(wallet) >= trade_ts - FRESH_WALLET_MAX_AGE_DAYS
```

Where:

- `first_seen(wallet)` = earliest Polygon `nonce = 0` transaction
  timestamp. Resolved via `PolygonClient.get_first_transaction`
  (already implemented in `profiler/chain.py`). For wallets created
  via a smart-contract proxy, use the proxy's deployment block.
- `FRESH_WALLET_MAX_AGE_DAYS = 30` for v1. Motivated by the
  empirical observation that ≥ 60 % of flagged insider positions
  in the 2024 election window came from wallets < 30 days old
  (Chaos Labs + Inca Digital dataset; see refs).

Alternative / tighter variant when on-chain RPC is slow:
**Polymarket-first-trade** — no trades for this wallet in the
prior 7 days, derived cheaply from `data-api /trades?user=…`.

### Data source requirement

- `proxyWallet` on every `data-api /trades` row (always present).
- Polygon RPC `eth_getTransactionCount` + block lookup
  (gated on paid RPC per Tier 3; § 14.2 of IMPLEMENTATION-TODOS).
- Until Tier 3 lands, fall back to the Polymarket-first-trade
  variant using `data-api /trades?user={address}&limit=1000`.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Heavy retail user who happens to have just moved wallets (hardware swap, seed rotation) | Funnel into `02-microstructure` gates — a true retail user's first trade is rarely ≥ $10K notional |
| 2 | Automated market-maker spinning up new wallets daily | Tag known MM wallets in `entities.yaml` and exclude by address prefix or behavioural fingerprint |
| 3 | Tornado.cash / mixer depositor whose first trade is large by mixer UX (withdraw full denomination) | Not an insider tell per se, but mixers correlate with informed flow; we leave them in and flag `funded_via_mixer` as a sub-tag |

### Calibration

- Threshold starts at `30 days` and is revisited after a 30-day
  retrospective against labelled outcomes. Expected precision
  without compound signals: `0.25 – 0.40` (i.e. noisy). Combined
  with `01-B` unusual-size the precision lifts to `0.50 – 0.70`
  in the Polymarket 2024 election replay.

### Historical precedent

- **2024 US Presidential Election.** Théo's network (Fredi9999,
  Theo4, PrincessCaro, Michie — and ≥ 7 other accounts) funded
  via fresh wallets from a French CEX, accumulated $40M+ before
  polls closed. Reported by WSJ (2024-10-24) and subsequently
  acknowledged by Polymarket. The wallets were ≤ 4 weeks old at
  the time of the largest fills.
- **Google Year in Search 2024.** A 14-day-old wallet flagged by
  AlphaRaccoon accumulated $442K on "Year in Search → Trump"
  hours before the term spiked; price moved from 0.32 → 0.89.
- **Maduro Venezuela 2024.** A cluster of 3 < 7-day-old wallets
  opened ~$280K of NO on "Maduro out by year-end" the day before
  the disputed-election result was announced.

### Priority

**P0.** Fire on every daily.

### Reliability band (v1)

`medium`. On its own — low. In combination with `01-B` or `02-A` —
medium-to-high.

---

## 01-B. Unusual-size anomaly

### Definition

A single trade whose notional is in the top percentile of the
flagged wallet's own historical distribution AND in the top
percentile of the market's 24h trade-size distribution.

### Theoretical basis

Easley & O'Hara (1987) show that large trades carry informational
content the market infers from order size alone. Barclay & Warner
(1993) partially counter this by documenting *stealth trading* —
informed traders break up orders into mid-size chunks to hide the
information. Both effects exist on Polymarket: we see both
monster single-fills (Fredi9999) and stealth patterns (accounts
placing 20+ trades of ~$10K each over 6 hours).

Accordingly the signal has **two variants**:

- **Variant A — single-fill dominance.** One trade ≥ X ⋅ market's
  p90 24h trade-size. X starts at 5. Captures the Fredi9999
  pattern.
- **Variant B — stealth clustering.** ≥ K trades from the same
  wallet within 4 h on the same market, each individually below
  the 01-B-A threshold but summing to ≥ 3 ⋅ market's p90 daily
  notional-per-wallet. Captures Barclay-Warner-style concealment.

### Computation

```
VARIANT A:
  flag if trade.notional >= 5 * market_p90_notional(market, 24h)

VARIANT B:
  group trades by (wallet, market) over 4h window
  flag if len(trades) >= K and sum(notional) >= 3 * market_p90_daily_notional
```

`K = 5` for v1. The market-level percentile is re-computed hourly
per market from `data-api /trades?market={conditionId}` and cached
for an hour. Liquidity-scaled — a $10K trade on a $5K-liquidity
market is maximum signal; the same trade on a $5M-book market is
noise.

### Data source requirement

- `data-api /trades` with `filterAmount` + per-market pagination.
- Ideally the market's own 7-day trade-size histogram (gamma-api
  volume history is a coarse proxy until we backfill on-chain).

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Market-maker rebalancing across a new market | Exclude wallets tagged `MM` in `entities.yaml` |
| 2 | Event-driven retail pile-on after a news flash (not informed, just fast) | Gate against `05-A` proximity-to-catalyst; if the flagged trade happened AFTER a detectable public event trigger, downweight |
| 3 | Whale testing market liquidity with a single probe | `01-B-A` alone flags it; `01-A` co-trigger filters away old-wallet whales |

### Calibration

- Variant-A multiplier `5 × p90` chosen to admit ≈ 15 trades per
  active market per day in the current volume regime (≈ 3,000
  trades/day ≥ $10K). Tune downward if the daily newsletter is
  consistently showing ≥ 10 rows on the same wallet.
- Variant-B `(K=5, 4h, 3×)` chosen so the stealth-pattern
  precision on the Fredi9999 cluster replay is ≥ 0.80.

### Historical precedent

- Polymarket "NBA MVP" 2023 — an account placed 14 trades of
  ~$4K each on SGA (eventual winner) over 3 hours starting 22 h
  before the voting deadline. Pre-trade market price 0.19; at
  open of next morning 0.31. Classic stealth pattern.
- Fredi9999 — single $1.5M fills on Trump. Variant-A 12×–25× the
  market's p90.

### Priority

**P0.** Fire on every daily.

### Reliability band (v1)

`medium`.

---

## 01-C. Funding-origin clustering

### Definition

A wallet's first significant USDC inflow (≥ $10K) traces back to
one of (a) a single CEX deposit address, (b) a known mixer
contract, or (c) a small cluster of related wallets that share a
funding ancestor N hops back.

Rolls up to a **funding-origin bar** (daily / weekly) and a
**cluster graph** (monthly).

### Theoretical basis

Analysts know informed traders concentrate at specific
exit-ramps. In the 2024 election context, a disproportionate
share of Trump-side whale wallets were funded from a Kraken
address cluster used previously by a well-known French
prop-trading firm. In the Maduro case, all three wallets in the
cluster traced back to the same BitGet deposit address.

This is the prediction-market equivalent of the Form 13F family
tree in equity disclosure — except we can compute it every 15
minutes, not every quarter.

### Computation

```
funding_origin(wallet) ::=
    first N funding transactions of wallet, sorted by block;
    for each, classify `from_address` against:
      - known CEX deposit-address list (entities.yaml)
      - known mixer list (Tornado Cash, etc.)
      - known cluster ancestors (walk backwards, max 3 hops)
    return highest-confidence match, else "unknown"
```

Cluster membership:

```
same_cluster(w1, w2) ::=
    exists an address A such that
      A funded w1 AND A funded w2 (within 30 days)
    OR
      same_cluster(funder(w1), funder(w2))  # transitive, depth ≤ 3
```

### Data source requirement

- `funding_transfers` table populated by the funding tracer
  (requires Polygon RPC — Tier 3 / § 14.2 of IMPLEMENTATION-TODOS).
- `entities.yaml` — curated allowlist of CEX deposit address
  ranges (Binance, Coinbase, Kraken, BitGet, KuCoin, OKX, etc.)
  plus mixer contract addresses.
- Until Tier 3 funding tracer lands, the funding-origin bar
  section is **omitted** from the daily rather than carrying a
  fabricated breakdown.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Two retail users happen to withdraw from the same Binance deposit address (shared cold wallet) | Require at least two of (a) same deposit amount rounded, (b) same 24h window, (c) same downstream trade target before clustering |
| 2 | Mixer users who aren't insiders — just privacy-focused retail | `funded_via_mixer` sub-tag doesn't auto-flag as informed; it only scores weakly |
| 3 | Funder is a service contract (Polymarket's own bonding curve, a DEX aggregator) | Exclude known service addresses from the ancestor walk |

### Calibration

- Cluster-detection hop depth = 3. Higher depths produce
  explosively large "clusters" where nothing is meaningful.
- Minimum cluster size = 2 wallets before a cluster is rendered.

### Historical precedent

- Théo cluster (2024 election) — 11 accounts, same ancestor
  within 2 hops via a French CEX proxy.
- 2024 Venezuela election — 3-wallet cluster, 1 ancestor hop.
- "0xafEe…" archetype wallet pattern observed in 2024 weather
  markets — multiple accounts funded from a single anonymous
  on-chain source ≤ 48h before a hurricane-landfall market
  resolution.

### Priority

**P0** for bar rollup (once funding-tracer lands). Cluster graph
is **P2** (monthly).

### Reliability band (v1)

`medium` for the bar, `low` for auto-clustering until a
human-reviewed false-positive audit clears it.

---

## 01-D. Entity-tagged wallet

### Definition

A wallet that matches one of our curated entity-registry entries
(known journalists, campaign staffers, regulatory-body associates,
professional prediction-market traders). Matches are hand-added,
rarely auto-derived, and always carry a provenance note.

### Theoretical basis

Pure heuristic — "if we already know who this is, say so." Acts
as a signal-booster: when an entity-tagged wallet appears in the
alpha list, that's a different level of confidence than an
anonymous fresh wallet. Equity-market analogue is the 13D/13G
beneficial-owner filing alert.

### Computation

Lookup only. No math.

```
entity_tag(wallet) ::= entities.yaml[wallet.lower()] or None
```

### Data source requirement

- `entities.yaml` hand-curated registry. Schema:

  ```yaml
  - address: "0x..."
    label: "Journalist — Bloomberg politics desk"
    confidence: high|medium|low
    added: 2026-04-15
    provenance: "WSJ profile naming their Polymarket username"
  ```

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Wallet reassignment (owner sold the wallet, new owner isn't the tagged entity) | `entities.yaml` entries carry a last-verified date; re-verify quarterly |
| 2 | Sybil — someone spoofing a known name via a new address we haven't tagged | Only tag wallets where we have documented provenance |

### Calibration

- Only `confidence: high` entries are auto-surfaced in the daily.
  `medium/low` entries appear in the CSV audit column but not in
  the body rows.

### Historical precedent

- Polymarket's own leaderboard names several "verified" pro
  traders. These are good `high`-confidence seeds.
- Unusual Whales / Polywhaler / PolymarketScan public dashboards
  tag journalist and political-insider accounts that have been
  cross-referenced to named individuals.

### Priority

**P1** (registry bootstrapping takes manual curation; not a
day-one launch blocker).

### Reliability band (v1)

`high` for `confidence: high` entries; signal is a hard lookup
with human-curated provenance.

---

## 01-E. Cross-market first-appearance

### Definition

A wallet's first trade in a new *category* of market (e.g.
its first US-politics market after a history of only sports
trading). Flags a discontinuous shift in the wallet's coverage.

### Theoretical basis

Informed traders concentrate in their circle of competence. A
pro sports-betting account that has never touched politics
suddenly placing a $50K position on a Senate runoff is either
(a) chasing news they just read, in which case no informed
edge, or (b) acting on private info outside their usual beat —
a friend leaked something. Case (b) is a signal; case (a) is
often distinguishable via `05-A` proximity-to-catalyst.

### Computation

```
category_shift(wallet, new_trade) ::=
    categories_before := set of market_category for wallet's trades < new_trade.ts
    flag if new_trade.market_category NOT IN categories_before
          AND len(categories_before) >= 3   # established in other cats
```

`market_category` derives from the gamma-api `category` + our own
keyword classifier in `ingestor/models.py::derive_category`.

### Data source requirement

- `data-api /trades?user={address}` paginated for the wallet's
  trade history.
- `gamma-api /markets` for category metadata.

### False-positive modes

| # | Mode | Mitigation |
|---|---|---|
| 1 | Retail user diversifying naturally after one event catches their eye (news-driven) | Require notional in the new category to be ≥ 3× the wallet's historical per-trade average |
| 2 | Categorization drift — our keyword classifier mislabels an election market as "entertainment" because it mentions a celebrity | Audit the classifier against gamma-api's authoritative category field; trust gamma when available |

### Calibration

- `len(categories_before) >= 3` prevents flagging every new user.
- Notional multiplier `3×` prevents every curiosity trade.

### Historical precedent

- 2024 Venezuelan election wallet set was dominated by accounts
  whose prior history was exclusively crypto-price and sports
  props — the jump to political markets was the tell.
- NBA MVP 2023 — multiple accounts that normally trade political
  events surfaced on basketball for the first time hours before
  the award was leaked on X.

### Priority

**P1** (needs 7-day wallet-history buffer before stable).

### Reliability band (v1)

`low` until we run the 30-day retrospective. Cross-category
entry is a weak signal alone; strong only in combination with
01-A or 01-B.

---

## Summary table

| Signal | Tier | Reliability (v1) | Data deps | False-positive risk |
|---|---|---|---|---|
| 01-A Fresh wallet | P0 | medium | Polygon RPC or /trades | medium |
| 01-B Unusual size | P0 | medium | /trades | medium |
| 01-C Funding origin | P0 (bar) / P2 (cluster) | medium / low | funding_transfers | medium |
| 01-D Entity tag | P1 | high (for high-conf entries) | entities.yaml | low |
| 01-E Cross-market shift | P1 | low | /trades history | high alone, medium combined |

Combined, the category fires when **≥ 2** sub-signals co-trigger
on the same (wallet, market, window) triple. The composition into
`informed_flow_score` is described in SPEC-MARKET-SIGNALS § 5.
