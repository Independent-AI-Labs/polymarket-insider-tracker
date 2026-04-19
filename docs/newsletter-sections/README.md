# Newsletter Section Research

Each document in this folder defines one of the four newsletter
sections the insider-tracker product ships. The README's signal
taxonomy (Fresh Wallets · Unusual Sizing · Niche Markets · Funding
Chains) is the anchor; each doc validates that signal against
real, cited Polymarket cases and states exactly what's already
implemented versus what still needs to be built.

| # | Section | Signal | Detector | Research |
|---|---------|--------|----------|----------|
| 1 | **New wallets, big bets** | Fresh Wallets | `detector/fresh_wallet.py` | [01-fresh-wallets.md](./01-fresh-wallets.md) |
| 2 | **High-conviction positions** | Unusual Sizing | `detector/size_anomaly.py` | [02-unusual-sizing.md](./02-unusual-sizing.md) |
| 3 | **Niche-market targeting** | Niche Markets | `detector/size_anomaly.py` (niche flag) | [03-niche-markets.md](./03-niche-markets.md) |
| 4 | **Entity-linked clusters** | Funding Chains | `profiler/funding.py` + `profiler/entities.py` | [04-funding-chains.md](./04-funding-chains.md) |

Each doc follows the same template:

1. **Thesis** — what the section is trying to surface for the reader.
2. **Validated cases** — every claim comes from a named wallet or
   handle, a specific market, a cited dollar amount, and a public
   source URL. No vibes.
3. **Implementation status** — file path + line references to the
   detector code, the existing tests, and any gaps.
4. **Ready-to-run test / next step** — either the exact pytest
   invocation that exercises the detector today, or a narrowly
   scoped next-step task with an estimated effort.
5. **Newsletter mock** — a draft of what the section renders as
   in the daily / weekly newsletter.

The research is deliberately concentrated in these docs (rather than
smeared across the README) so the "what the product claims it does"
story stays grounded in real cases that readers can audit.
