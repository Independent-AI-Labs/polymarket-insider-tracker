# BACKFILL-CAVEATS

**Status:** draft
**Date:** 2026-04-21
**Consumed by:** `scripts/backfill-snapshots.py`

Catalogue of replay-safety caveats discovered while wiring the
historical backloader. Each row documents a signal or shared helper
that reads live-state fields or uses wall-clock time, and the
mitigation the backfill script applies.

---

## Signals skipped by default in replay mode

Listed in `REPLAY_SKIP_SIGNALS` at the top of
`scripts/backfill-snapshots.py`. A signal lands here when its
compute path reads a field that is valid for "today" only and
cannot be reconstructed from archival trades alone.

| Signal id | Why skipped | What it would need for replay-safety |
|---|---|---|
| `03-A-volume-velocity` | Reads `market_meta["volume24hr"]`, a live gamma field. In replay we set `volume24hr = 0` for every market (we don't have a historical snapshot), so the signal would either produce zero hits or spurious hits keyed to today's volume. | Historical daily volumes per market (not exposed by gamma). Would require rolling the 24h sum ourselves from the archival trade stream. |

Users can add more via `--skip-signal <id>` (repeatable). The flag
set is applied by in-place mutation of
`detector.signals.registry.REGISTRY` — composer reads that list
directly, so mutating it pre-compose is the minimum-intrusion way
to filter.

---

## Signals that run in replay but degrade

These still produce hits, but fewer than they would have on the
original live day. Documented for interpretation, not silenced.

| Concern | Affected signals | Degradation |
|---|---|---|
| `gates.has_enough_time_to_close` uses `datetime.now(UTC)`. Markets that closed between day D and the replay date fail the 24 h-to-close gate. | Every signal using `DEFAULT_GATES` with `require_time=True`: fresh-wallet, unusual-size, OFI, stealth-cluster. | Fewer eligible markets per historical day than the live newsletter observed. The signal set shrinks monotonically — no false positives, just more false negatives. |
| `market_meta["liquidityClob"]` is live-only. Signals that use the thin-book gate (`require_liquidity=True`) get `0` at replay, which fails the ratio check. | Thin-book gate (spec § 03-D). None of the current P0 signals enable `require_liquidity`. | No impact on P0. If Phase 3 turns on a liquidity-gated signal, it will produce zero replay hits. |
| `market_meta["lastTradePrice"]` is the current price, not the price at D. The `price_in_band` gate uses it to filter extreme-price markets. | All P0 signals — `require_price=True` is the default. | Markets that finished in the extreme tails but traded mid-band during D get dropped. Markets that finished mid-band but traded extreme during D get kept. Net bias depends on the specific day; we accept this as a known signal-recall tax. |

For Phase 3 empirical pruning this is acceptable: the comparison
across signals uses relative hit rates, and every signal is
subjected to the same gate-degradation, so the pruning ordering is
preserved.

---

## Signals that perform well in replay

| Signal id | Notes |
|---|---|
| `01-A-fresh-wallet` | Makes a per-wallet `/trades?user=<addr>` call for "first seen" timestamp. That endpoint returns a wallet's all-time trade history, so the oldest timestamp is valid. Gated against `context.window_end` (honors replayed `now`). Cost: one extra API call per unique replay-wallet, cached across the script's lifetime via `_wallet_cache`. |
| `01-B-unusual-size` | Pure within-window calculation off per-market p90. Fully replay-safe. |
| `02-A-order-flow-imbalance` | Pure within-window calculation. Fully replay-safe. |
| `02-C-stealth-cluster` | Pure within-window calculation. Fully replay-safe. |

---

## Market-meta fields populated by the backfill

The backfill script synthesises a minimal gamma-api row per market
from the enum pass. Fields set vs absent:

| Field | Value in replay |
|---|---|
| `conditionId`, `question`, `slug` | Authoritative (from gamma enum). |
| `startDate`, `endDate`, `closed`, `volumeNum` | Authoritative. |
| `volume24hr` | `0` (live-only; see caveat above). |
| `liquidityClob` | `0` (live-only). |
| `category` | `""` (the gamma enum doesn't return it cheaply; signals that check novelty categories see no match, which falls back to slug-keyword novelty filtering). |
| `lastTradePrice` | Synthesised as the price of the latest trade we observed for that market inside the backfill window. Populating this is necessary — the price-band gate returns `False` when the field is missing, which kills every signal. Approximation bias: markets with no trades in-window get no entry, so they drop out of the price-band filter entirely (rather than being assessed at their live price). |
| `bestBid`, `bestAsk` | Absent — `price_in_band` falls back to `lastTradePrice` above, which is populated. |

---

## Truncation

The `data-api.polymarket.com/trades?market=<cid>` endpoint enforces
the same 3000-offset ceiling as the global `/trades` feed. Markets
with > 3000 trades inside the backfill window are truncated at
their earliest-reachable page; the backloader logs a WARNING per
occurrence and increments a truncation counter. Truncated markets
are NOT excluded from the replay — their partial trade set is
bucketed by day as usual; the earliest days of the window simply
see a smaller contribution from that market.

Heavy markets (presidential elections, major sports finals) are
the main source of truncation. For Phase 3 purposes this is
acceptable: the truncation bias is concentrated in top-of-book
markets that Phase 3 already knows are outliers.
