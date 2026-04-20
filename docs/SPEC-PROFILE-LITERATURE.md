# SPEC-PROFILE-LITERATURE — wallet + market profiling: cited dimensions survey

**Status:** Phase 1 (literature + in-production survey) of the profile system
**Authors:** claude-operator + vlad
**Date:** 2026-04-20
**Companion docs:** `docs/SPEC-MARKET-SIGNALS.md` (signal taxonomy this
profiles layer atop), plan file `mellow-enchanting-melody.md`
(Phase 1 scope). Subsequent phase docs —
`SPEC-PROFILE-CANDIDATES.md`, `SPEC-PROFILE-ITERATION-3.md`,
`SPEC-PROFILE-VISUAL.md` — cite entries here by name.

---

## 1. Purpose

Before we propose a single stat for the Wallet Profile or the Market
Profile, we enumerate every profiling dimension already present in
(a) peer-reviewed market-microstructure and behavioural-finance
literature, (b) in-production on-chain analytics and surveillance
systems, and (c) prediction-market-specific academic work. The
deliverable is a cited dimension catalogue, not a stat proposal.

The goal at this stage is coverage, not selection. Phase 2 of the
profile-system plan prunes from this catalogue into a candidate stat
pool; Phase 3 empirically prunes that pool further; only Phase 4
commits a YAML schema. This document exists so those later phases
cite *something* rather than reinventing.

Out of scope here: any ranking of dimensions, any commitment to
computing one over another, any data-input plumbing, any visual
decision. All four are deferred.

## 2. Taxonomy at a glance

| Bucket | Section | What it catalogues | Entries |
|---|---|---|---|
| A | § 3 | Academic microstructure / behavioural-finance dimensions with a canonical published source | 14 |
| B | § 4 | In-production wallet / surveillance profilers — what commercial and regulatory systems actually expose | 8 |
| C | § 5 | Prediction-market-specific academic work, with emphasis on Polymarket 2024-2026 | 5 |
| — | § 6 | Synthesis — dimensions that recur across buckets, i.e. Phase-2 leading candidates | — |

Total: 27 distinct works / systems cited.

## 3. Bucket A — academic microstructure and behavioural finance

Scope: measures with a canonical peer-reviewed source, primarily
from equity or FX markets, that translate to Polymarket either
verbatim (most) or with the binary-payoff reinterpretation from
`SPEC-MARKET-SIGNALS.md` § 2. Each entry names the measure as it
appears in the literature, cites the primary source with a DOI or
stable URL, gives a one-paragraph explanation and a formula or
algorithmic sketch, states whether it applies to market-level,
wallet-level, or both, and lists its required data inputs.

### 3.1 Kyle's λ — price-impact coefficient

- **Source:** Kyle, A. S. (1985). "Continuous Auctions and Insider
  Trading." *Econometrica* 53(6): 1315-1335.
  [JSTOR 1913210](https://www.jstor.org/stable/1913210).
- **What it measures:** the linear price-impact sensitivity in the
  informed-trader equilibrium. A higher λ means each unit of signed
  order flow moves the mid-price more, i.e. the market is thinner
  or more informed.
- **Formula / sketch:** regress per-trade mid-price change on signed
  notional: `ΔP_t = λ · q_t + ε_t` where `q_t` is signed dollar
  volume of trade `t`. Fit per-market over a rolling window.
- **Level:** market-level (one λ per market); wallet-level
  contribution share = fraction of aggregate signed-flow-impact
  attributable to that wallet's trades.
- **Inputs:** per-trade price, signed notional, timestamp.

### 3.2 Amihud illiquidity `ILLIQ`

- **Source:** Amihud, Y. (2002). "Illiquidity and stock returns:
  cross-section and time-series effects." *Journal of Financial
  Markets* 5(1): 31-56.
  [doi:10.1016/S1386-4181(01)00024-6](https://doi.org/10.1016/S1386-4181(01)00024-6).
- **What it measures:** the daily absolute return per dollar of
  trading volume; a rough, low-frequency price-impact proxy for
  markets without a clean trade-by-trade feed.
- **Formula:** `ILLIQ_d = |R_d| / V_d`; market `ILLIQ` is the mean
  across days in the window.
- **Level:** market-level.
- **Inputs:** daily return `R_d`, daily dollar volume `V_d`.

### 3.3 PIN — probability of informed trading

- **Source:** Easley, D., Kiefer, N. M., O'Hara, M., & Paperman,
  J. B. (1996). "Liquidity, Information, and Infrequently Traded
  Stocks." *Journal of Finance* 51(4): 1405-1436.
  [doi:10.1111/j.1540-6261.1996.tb04074.x](https://doi.org/10.1111/j.1540-6261.1996.tb04074.x).
- **What it measures:** the posterior probability that a random
  trade originates from an informed trader, inferred from the
  imbalance between buyer- and seller-initiated trade counts.
- **Formula:** fit the EKOP structural model with arrival rates
  (`α`, `δ`, `μ`, `ε`) via max-likelihood on buy/sell trade-count
  panels; `PIN = α·μ / (α·μ + 2·ε)`.
- **Level:** market-level.
- **Inputs:** buy-initiated and sell-initiated trade counts per day
  (or other aggregation bucket).

### 3.4 VPIN — volume-synchronised PIN

- **Source:** Easley, D., López de Prado, M., & O'Hara, M. (2012).
  "Flow Toxicity and Liquidity in a High-Frequency World."
  *Review of Financial Studies* 25(5): 1457-1493.
  [doi:10.1093/rfs/hhs053](https://doi.org/10.1093/rfs/hhs053);
  SSRN preprint [1695596](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1695596).
- **What it measures:** a model-free proxy for order-flow toxicity,
  reading the absolute imbalance between buy and sell volume across
  equal-volume buckets.
- **Formula:** divide trading into `N` buckets of equal volume `V`,
  classify each trade's volume as buy- or sell-side (BVC bulk
  volume classification), compute
  `VPIN = (1/N) · Σ |V_buy_i − V_sell_i| / V`.
- **Level:** market-level.
- **Inputs:** trade stream with price + size; works without
  explicit trade-direction labels via bulk classification.

### 3.5 Barclay-Warner stealth-trading index

- **Source:** Barclay, M. J. & Warner, J. B. (1993). "Stealth trading
  and volatility: which trades move prices?" *Journal of Financial
  Economics* 34(3): 281-305.
  [doi:10.1016/0304-405X(93)90029-B](https://doi.org/10.1016/0304-405X(93)90029-B).
- **What it measures:** the share of a security's cumulative price
  change attributable to *medium-size* trades, relative to their
  share of volume. A high share indicates informed traders hiding
  in medium-size clips rather than large visible prints.
- **Formula:** cumulative price change contributed by
  medium-size-bucket trades ÷ that bucket's share of total volume,
  where "medium" is typically 5 %-ile to 95 %-ile of the per-market
  size distribution (or published bucket thresholds).
- **Level:** market-level as originally defined; can be recast as a
  wallet-level stat (fraction of a wallet's volume in the medium
  bucket, relative to their own base rate) for behavioural profiling.
- **Inputs:** trade sizes, signed price changes, per-market size
  distribution.

### 3.6 Adverse-selection spread component (Glosten-Milgrom)

- **Source:** Glosten, L. R. & Milgrom, P. R. (1985). "Bid, ask and
  transaction prices in a specialist market with heterogeneously
  informed traders." *Journal of Financial Economics* 14(1): 71-100.
  [doi:10.1016/0304-405X(85)90044-3](https://doi.org/10.1016/0304-405X(85)90044-3).
- **What it measures:** the portion of the bid-ask spread that
  compensates the market maker for trading against better-informed
  counterparties. Larger adverse-selection spreads imply more
  informed flow.
- **Formula / sketch:** decompose the effective spread into
  adverse-selection, inventory, and order-processing components
  via a Huang-Stoll or extended Glosten-Harris regression of
  signed trades on subsequent mid-quote revisions.
- **Level:** market-level; per-wallet contribution quantifiable
  as the wallet's share of trades that preceded large mid-quote
  revisions in the expected direction.
- **Inputs:** trade prints, contemporaneous best-bid/best-ask or
  mid-quote series, trade signs.

### 3.7 Hasbrouck information share

- **Source:** Hasbrouck, J. (1995). "One Security, Many Markets:
  Determining the Contributions to Price Discovery."
  *Journal of Finance* 50(4): 1175-1199.
  [doi:10.1111/j.1540-6261.1995.tb04054.x](https://doi.org/10.1111/j.1540-6261.1995.tb04054.x).
- **What it measures:** when the same security trades in multiple
  venues, the share of innovations in the common efficient price
  attributable to each venue. Natural Polymarket mapping: venues →
  logically-equivalent markets (e.g. two worded variants of the
  same outcome) or cross-venue (Polymarket vs. Kalshi, deferred).
- **Formula:** fit a VECM on multi-venue mid-prices with a common
  cointegrating vector; compute each venue's share of the variance
  of the common-factor innovation.
- **Level:** market-level across related markets; wallet-level
  lead-lag variant: time between a wallet's entry and the next 5 %
  move elsewhere.
- **Inputs:** aligned mid-price series from ≥ 2 related markets.

### 3.8 Roll's implied spread

- **Source:** Roll, R. (1984). "A Simple Implicit Measure of the
  Effective Bid-Ask Spread in an Efficient Market."
  *Journal of Finance* 39(4): 1127-1139.
  [doi:10.1111/j.1540-6261.1984.tb03897.x](https://doi.org/10.1111/j.1540-6261.1984.tb03897.x).
- **What it measures:** the effective spread inferred from the
  first-order negative serial covariance of price changes — a
  trade-free spread estimator.
- **Formula:** `spread ≈ 2 · √(−Cov(ΔP_t, ΔP_{t−1}))` when the
  covariance is negative; undefined (or set to zero) when positive.
- **Level:** market-level.
- **Inputs:** trade-price time series.

### 3.9 Corwin-Schultz high-low spread

- **Source:** Corwin, S. A. & Schultz, P. (2012). "A Simple Way
  to Estimate Bid-Ask Spreads from Daily High and Low Prices."
  *Journal of Finance* 67(2): 719-760.
  [doi:10.1111/j.1540-6261.2012.01729.x](https://doi.org/10.1111/j.1540-6261.2012.01729.x).
- **What it measures:** the effective spread inferred from the
  ratio of high-low ranges over 1-day vs. 2-day windows, exploiting
  that highs are almost always buys and lows almost always sells.
- **Formula:** `β = [ln(H_t/L_t)]² + [ln(H_{t+1}/L_{t+1})]²`,
  `γ = [ln(max(H_t,H_{t+1}) / min(L_t,L_{t+1}))]²`,
  `α = (√(2β) − √β) / (3 − 2√2) − √(γ / (3 − 2√2))`,
  spread = `2·(e^α − 1) / (1 + e^α)`.
- **Level:** market-level.
- **Inputs:** daily high and low trade prices.

### 3.10 Realised volatility

- **Source:** Andersen, T. G., Bollerslev, T., Diebold, F. X. &
  Labys, P. (2001). "The Distribution of Realized Exchange Rate
  Volatility." *Journal of the American Statistical Association*
  96(453): 42-55.
  [doi:10.1198/016214501750332965](https://doi.org/10.1198/016214501750332965).
- **What it measures:** the square-root of the sum of squared
  high-frequency returns over a window — a consistent, model-free
  estimator of integrated variance.
- **Formula:** `RV_w = √Σ_i r_i²` where `r_i` are within-window
  log-returns (or absolute returns for the binary-payoff
  reinterpretation of Polymarket prices).
- **Level:** market-level.
- **Inputs:** intra-window price series.

### 3.11 Herfindahl-Hirschman index (HHI)

- **Source:** Hirschman, A. O. (1945). *National Power and the
  Structure of Foreign Trade.* University of California Press;
  rediscovered in antitrust via Herfindahl (1950, unpublished PhD
  dissertation, Columbia University). For a stable published
  re-derivation see Hirschman, A. O. (1964). "The Paternity of an
  Index." *American Economic Review* 54(5): 761-762.
  [JSTOR 1818582](https://www.jstor.org/stable/1818582).
- **What it measures:** concentration of a distribution. In profile
  use: (market-level) concentration of volume across participants,
  (wallet-level) concentration of that wallet's activity across
  markets or categories. Low HHI = broad, high = concentrated.
- **Formula:** `HHI = Σ s_i²` over participant shares `s_i` summing
  to 1.
- **Level:** both — market-level participant breadth and
  wallet-level market/category breadth.
- **Inputs:** per-participant or per-market volume shares.

### 3.12 Pástor-Stambaugh liquidity factor

- **Source:** Pástor, Ľ. & Stambaugh, R. F. (2003). "Liquidity Risk
  and Expected Stock Returns." *Journal of Political Economy*
  111(3): 642-685.
  [doi:10.1086/374184](https://doi.org/10.1086/374184).
- **What it measures:** a reversal-based liquidity measure: the
  regression coefficient on signed prior-day volume in a predictive
  regression for returns. Captures the temporary-price-impact that
  reverses vs. the permanent component.
- **Formula:** `r_{d+1} = θ + φ·r_d + γ·sign(r_d)·V_d + ε_{d+1}`;
  `γ` is the liquidity measure (more negative = less liquid).
- **Level:** market-level; market-wide factor requires aggregation
  we do not attempt in-profile.
- **Inputs:** daily returns, daily dollar volume.

### 3.13 Trade-size distribution forensics

- **Source:** Cong, L. W., Li, X., Tang, K., & Yang, Y. (2023).
  "Crypto Wash Trading." *Management Science* 69(11): 6427-6454.
  [doi:10.1287/mnsc.2021.02709](https://doi.org/10.1287/mnsc.2021.02709);
  SSRN [3530220](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3530220).
- **What it measures:** statistical regularities of authentic
  trade-size distributions — first-significant-digit (Benford) fit,
  tail-index behaviour, round-number clustering. Deviation
  indicates manipulated or wash flow.
- **Formula / sketch:** goodness-of-fit tests (χ² on Benford
  first-digit frequencies), Pareto tail-index via Hill estimator,
  size-rounding concentration at round thresholds (e.g. $100,
  $1000). Paper applies these to identify centralised exchanges
  with ~70 %+ fabricated volume.
- **Level:** both — market-level volume forensics, wallet-level
  size-fingerprint check.
- **Inputs:** per-trade notional distribution; for wallet-level,
  the wallet's own distribution vs. the market baseline.

### 3.14 Disposition effect

- **Source:** Shefrin, H. & Statman, M. (1985). "The Disposition
  to Sell Winners Too Early and Ride Losers Too Long: Theory and
  Evidence." *Journal of Finance* 40(3): 777-790.
  [doi:10.1111/j.1540-6261.1985.tb05002.x](https://doi.org/10.1111/j.1540-6261.1985.tb05002.x).
- **What it measures:** the behavioural tendency to realise gains
  quickly while holding losses. Operationalised per trader as the
  proportion of gains realised vs. proportion of losses realised.
- **Formula:** `PGR = RG / (RG + PG)`, `PLR = RL / (RL + PL)`;
  disposition = `PGR − PLR`; `RG/PG` and `RL/PL` are realised and
  paper gains/losses.
- **Level:** wallet-level.
- **Inputs:** per-wallet opened/closed positions with entry and
  exit prices. Requires Tier-3 chain indexer — flagged as future
  stat in `plan §"Out of scope"`.

### Dropped from the starter list

- **Turnover (Fama-MacBeth 1973).** Turnover is a definitional
  ratio (`gross volume / capital at risk`), not a cited
  dimension of the 1973 Fama-MacBeth paper, which is a
  cross-sectional-regression methodology. Included in Phase 2 as
  a definitional stat without an academic citation rather than
  forced into Bucket A.

## 4. Bucket B — in-production wallet profilers

Scope: commercial and regulatory systems that already expose
wallet- or account-level profiling dimensions. We cite public
documentation — product pages, academy articles, or publicly-posted
methodology — and explicitly note proprietary black-box pieces.
Marketing fluff without technical specifics is excluded.

| System | Domain | Dimensions exposed | Public / proprietary | Reference |
|---|---|---|---|---|
| Nansen | DeFi analytics | ~400 categorical labels (Smart Money, Fund, Whale, MEV Bot, CEX Hot/Cold, Mixer, Emerging Smart Trader, Sector Specialist); Smart-DEX-Trader tiers keyed to cumulative realised PnL (≥ $1.5 M = top-0.1 %); per-label timeframe variants. Numeric: PnL, ROI, win-rate. | Labels and tier thresholds are public; the automated-identification model is proprietary | [What is Smart Money — Nansen methodology](https://www.nansen.ai/guides/what-is-smart-money-in-crypto-a-detailed-look-into-our-methodology); [Labels & Watchlists 101](https://academy.nansen.ai/articles/2149924-labels-and-watchlists-101) |
| Arkham Intelligence | DeFi analytics / investigations | Entity grouping (many addresses → one entity), entity-level P&L from cost basis at time of transfer, holdings-graph, custom labels (user and platform), counterparty graph, per-token balance-over-time | Entity framework and P&L mechanics public; attribution inference proprietary | [Arkham Codex — Profiler](https://codex.arkm.com/the-intelligence-platform/profiler); [Private Labels](https://codex.arkm.com/the-intelligence-platform/private-labels) |
| Zerion | Retail portfolio tracker | Wallet age, net worth, realised ROI, win-rate, trade count, token allocation, protocol exposure | Public numeric dimensions; ranking logic proprietary | [Zerion API docs](https://developers.zerion.io/reference/intro) |
| DeBank | Retail / portfolio | Total net worth across 56+ chains, portfolio allocation, transaction count, per-token action labels (send/receive/swap/approve/multicall), TVF (total value of followers) social metric, approval-risk exposure | Core features public; "DeBank Profile" reputation system is proprietary | [DeBank developer docs](https://docs.cloud.debank.com/en/readme) |
| Chainalysis Reactor | Regulatory / law enforcement | Per-address *direct exposure* (counterparties by service / entity category) and *indirect exposure* (services reached by tracing through non-service hops); aggregate risk score; exposure pie ("exposure wheel"); graph-pattern visualisation | Dimensions and methodology public; entity-labelling database proprietary | [Chainalysis — indirect exposure](https://www.chainalysis.com/blog/cryptocurrency-risk-blockchain-analysis-indirect-exposure/); [Reactor product](https://www.chainalysis.com/product/reactor/) |
| TRM Labs | Regulatory / compliance | Counterparty-chain risk scoring, illicit-exposure propagation, entity clustering — structurally similar to Chainalysis Reactor | Dimensions public; risk-score weights proprietary | [TRM — risk scoring](https://www.trmlabs.com/products/forensics) |
| FINRA SMARTS-equivalent surveillance patterns | Regulatory surveillance (equities) | Alert-shaped indicators: wash-trade, layering, spoofing, marking-the-close, momentum-ignition, front-running, trading-ahead, prearranged-trades, cross-product manipulation. Firm-level FINRA Rule 3110/3120 compliance requires documented detection for each | Patterns and supervisory expectations public via FINRA Regulatory Oversight Reports; exact vendor thresholds proprietary | [FINRA 2025 Regulatory Oversight Report — Manipulative Trading](https://www.finra.org/rules-guidance/guidance/reports/2025-finra-annual-regulatory-oversight-report/manipulative-trading) |
| Polymarket-ecosystem profilers (Polywhaler, Polymarket Alerts, Unusual Predictions) | Retail / community | Ad-hoc combinations of: fresh-wallet flag, whale threshold (e.g. ≥ $10k single trade), win-rate on resolved markets, all-in concentration on a single market, funding-origin tag | Public feed; thresholds hardcoded and undocumented | [Polywhaler / Polymarket Alerts public feeds — see `docs/SPEC-DATA-SOURCES.md` § community trackers] |

Takeaway: across systems the recurring numeric dimensions are
(a) net worth / capital at risk, (b) realised PnL, (c) win-rate,
(d) counterparty-exposure risk, (e) age / tenure. The recurring
categorical dimensions are (f) entity type / label, (g) behaviour
class (whale / sniper / MEV bot / mixer), (h) manipulation-pattern
flags (wash, spoof, layer). Bucket A formalises what several of
these informally proxy (HHI for concentration, Kyle's λ for
price-impact contribution, disposition for win-rate asymmetry).

## 5. Bucket C — Polymarket-specific and prediction-market academic

Scope: work that studies prediction markets directly, with priority
to Polymarket 2024-2026 papers that post-date the starter list.

### 5.1 Wolfers & Zitzewitz (2004) — prediction-market survey

- **Citation:** Wolfers, J. & Zitzewitz, E. (2004). "Prediction
  Markets." *Journal of Economic Perspectives* 18(2): 107-126.
  [doi:10.1257/0895330041371321](https://doi.org/10.1257/0895330041371321);
  NBER WP [10504](https://www.nber.org/papers/w10504).
- **Profiling dimensions extracted:** participant heterogeneity
  (pro vs. retail, hedger vs. speculator), timing-concentration
  near resolution, calibration of market prices to realised
  frequencies.
- **Mapping:** justifies wallet-level tenure, market-level
  time-to-resolution, and participant-heterogeneity decomposition
  as first-class profile axes. Foundational — every later paper
  cites back.

### 5.2 Page & Siemroth (2021) — information-incorporation decomposition

- **Citation:** Page, L. & Siemroth, C. (2021). "How Much
  Information Is Incorporated into Financial Asset Prices?
  Experimental Evidence." *Review of Financial Studies* 34(9):
  4412-4449.
  [doi:10.1093/rfs/hhaa143](https://doi.org/10.1093/rfs/hhaa143).
  (The 2020 version previously cited in the plan is the working
  paper; this is the published *RFS* version with the same
  methodology.)
- **Profiling dimensions extracted:** a decomposition of PIN into
  public-info vs. private-info incorporation; finding that < 50 %
  of private information is reflected in prices.
- **Mapping:** motivates market-level PIN / VPIN as profile axes
  and validates the wallet-level "informed-trader" decomposition
  underlying the sniper detector.

### 5.3 Tsang & Yang (2026) — anatomy of Polymarket, 2024 election

- **Citation:** Tsang, K. P. & Yang, Z. (2026). "The Anatomy of
  Polymarket: Evidence from the 2024 Presidential Election."
  arXiv [2603.03136](https://arxiv.org/abs/2603.03136).
- **Profiling dimensions extracted:** three decomposed volume
  measures — *exchange-equivalent trading volume*, *net inflow*,
  *gross market activity* — that separate genuine risk-transfer
  from share-minting / burning / conversion, which are structurally
  different on Polymarket than on equities. Identifies whale-trader
  emergence in October 2024 as a turning-point profile feature.
- **Mapping:** directly informs how wallet-level gross notional is
  computed on Polymarket (must account for mint/burn vs. exchange
  leg) and provides an empirical reference for market-level
  participant-structure shifts near high-salience events.

### 5.4 Ng, Peng, Tao & Zhou (2025) — cross-venue price discovery

- **Citation:** Ng, H., Peng, L., Tao, Y. & Zhou, D. (2025).
  "Price Discovery and Trading in Modern Prediction Markets."
  SSRN [5331995](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5331995).
- **Profiling dimensions extracted:** net-order-imbalance from
  *large trades* predicts subsequent returns; the venue with
  greater directional large-trade flow leads price discovery
  across Polymarket / Kalshi / PredictIt / Robinhood on matched
  contracts.
- **Mapping:** strengthens the case for a wallet-level
  large-trade-imbalance stat (tagged as cross-venue lead-lag
  ability) and for market-level Hasbrouck-style information-share
  between logically-linked contracts.

### 5.5 Reichenbach & Walther (2025) — accuracy, skill, and bias

- **Citation:** Reichenbach, F. & Walther, M. (2025). "Exploring
  Decentralized Prediction Markets: Accuracy, Skill, and Bias on
  Polymarket." SSRN
  [5910522](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5910522).
- **Profiling dimensions extracted:** per-wallet skill decomposition
  across 124 M Polymarket trades; separates calibration skill from
  resolution skill; documents systematic biases by market category.
- **Mapping:** provides empirical benchmarks for wallet-level
  realised-ROI and win-rate distributions — the base rates Phase 3
  pruning will compare candidate skill stats against.

## 6. Synthesis

Across Buckets A, B, and C, four dimension families recur
independently in each bucket, which makes them the leading
candidates for Phase 2's pool.

1. **Price-impact contribution.** Kyle 1985 formalises it (§ 3.1),
   Amihud 2002 gives a low-frequency proxy (§ 3.2), Arkham and
   Chainalysis expose analogous entity-level flow magnitudes, and
   Ng et al. 2025 (§ 5.4) establish that large-trade imbalance
   drives Polymarket price discovery. At both market and wallet
   levels.
2. **Order-flow toxicity / informedness.** PIN (§ 3.3), VPIN
   (§ 3.4), adverse-selection spread (§ 3.6), Barclay-Warner
   stealth (§ 3.5), and Page-Siemroth (§ 5.2) all measure the
   same latent — the degree to which flow comes from better-informed
   counterparties. In-production surveillance surfaces this as
   manipulation-pattern flags (FINRA § 4). Mostly market-level,
   with decomposable wallet-level contribution shares.
3. **Concentration / breadth.** HHI (§ 3.11) is the canonical
   measure; Tsang-Yang (§ 5.3) documents participant-concentration
   shifts on Polymarket, and Nansen / Arkham expose portfolio
   allocation and entity-grouping as breadth analogues. Applies
   symmetrically at market level (participant concentration) and
   wallet level (market / category concentration).
4. **Behavioural asymmetry / skill.** Shefrin-Statman disposition
   (§ 3.14), Nansen's Smart-DEX-Trader tiers (§ 4), and Reichenbach-
   Walther (§ 5.5) all isolate realised outcomes of wallet
   behaviour. Wallet-level; gated on Tier-3 resolution data.

Two further dimensions recur but only at market level: **effective
spread** (Roll § 3.8, Corwin-Schultz § 3.9) and **realised
volatility** (§ 3.10). **Trade-size forensics** (Cong et al. § 3.13)
recurs at both levels but is antagonistic in framing — it detects
*inauthentic* rather than *informed* flow, so Phase 2 will have to
decide whether to treat it as a profile axis or a separate
signal-class guard.

Phase 2 enumerates computation-ready candidates from each family
above; redundancy vs. correlation pruning is Phase 3's job.
