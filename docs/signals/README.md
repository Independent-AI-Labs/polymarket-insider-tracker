# Signals index

Per-category specs that implement the taxonomy in
`docs/SPEC-MARKET-SIGNALS.md`. Each doc defines a family of
signals with:

- Definition + theoretical basis (academic reference where
  applicable)
- Computation (formula + inputs)
- Data source requirement (cross-referenced against
  `docs/SPEC-DATA-SOURCES.md`)
- False-positive modes + mitigations
- Calibration notes
- Historical precedent on Polymarket
- Priority tier (P0-P3) + reliability band (v1)

---

## Categories

1. [**01 — Informed-flow fingerprints**](01-informed-flow.md) —
   who is the informed trader? Fresh wallet, unusual size,
   funding origin, entity-tagged, cross-market first-appearance.
2. [**02 — Market microstructure**](02-microstructure.md) —
   shape of order flow: OFI, VPIN-adapted, stealth clustering,
   price-impact asymmetry.
3. [**03 — Volume + liquidity**](03-volume-liquidity.md) —
   velocity, taker/maker split, book-depth imbalance, thin-book
   gate.
4. [**04 — Price dynamics**](04-price-dynamics.md) —
   breakout, divergence, autocorrelation, mean-reversion
   failure.
5. [**05 — Event catalyst**](05-event-catalyst.md) —
   proximity-to-resolution, scheduled-event windows, news
   correlation (roadmap).
6. [**06 — Cross-market consistency**](06-cross-market.md) —
   multi-outcome arithmetic, containment, cross-venue
   arbitrage, correlated-markets co-movement.

## Implementation roadmap

See `docs/IMPLEMENTATION-PLAN-SIGNALS.md` for phased rollout
with dependency manifest and exit gates.

## Calibration log

Per-phase calibration results (once signals are live) land under
`docs/signals/CALIBRATION-{date}.md`.

## Non-signals (retired)

Explicitly excluded — see SPEC-MARKET-SIGNALS § 6:

- "Near-certain" observations (price < 0.05 or > 0.95 alone)
- "Top liquidity" market ranking
- "Recently created" markets section
- "Thin-book ratio" as standalone observation
