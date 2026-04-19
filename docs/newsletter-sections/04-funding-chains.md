# Section 4 — "Entity-linked clusters" (Funding Chains)

## Thesis

Follow the USDC backwards. Fresh wallets that look independent in
isolation often consolidate to the same origin — a single CEX
withdrawal, a common funding wallet, or a deterministic
address-derivation scheme. When the clustering is tight, one
"suspicious wallet" is actually one of many faces of the same
informed trader. This section surfaces the graph, not the nodes.

## Validated cases

### Case 4.1 — "Théo" / Fredi9999 (2024 election)

- **What happened**: a single French trader nicknamed "Théo"
  controlled **at least eleven separate Polymarket accounts** —
  the public handles included Fredi9999, Theo4, PrincessCaro,
  zxgngl — and wagered **$70M+** on Trump's victory for **$85M
  profit**. Those 11 accounts held 25% of Trump Electoral College
  contracts and **40%+ of Trump popular-vote contracts**.
- **How the cluster was identified**: Chainalysis used wallet
  clustering heuristics on transaction patterns, timing, funding
  sources, and behavioural signatures — exactly the mix of
  features the funding-chain + relationship-graph stack here
  targets.
- **Obfuscation technique documented**: high-frequency trading
  (1,600+ trades/24h during peaks), mixing large $4,302
  transactions with small $0.30-$187 orders to dilute cluster
  analysis.
- **Sources**: [Medium · Josh W, "Polymarket: How Crypto Prediction Markets Legalized Insider Trading"](https://medium.com/@josh.insidertrading.tech/polymarket-how-crypto-prediction-markets-legalized-insider-trading-1665fe9e8598).

### Case 4.2 — Axiom pre-reveal cluster

- **What happened**: the 12 fresh wallets from Section 1's Axiom
  case shared a common funding window and — per Lookonchain —
  traced back through USDC transfers to overlapping origin
  wallets, some of which resolved to CEX hot wallets.
- **Why this is a funding-chain case**: the individual wallets
  each looked fresh, but the collective behaviour was only
  decodable through backwards USDC traversal.

### Case 4.3 — Commercial tool validation

- **Chaincatcher** and [walletfinder.ai](https://www.walletfinder.ai/blog/how-wallet-clusters-signal-market-shifts)
  describe the same technique we use: "graph-based cluster
  analysis treats blockchain addresses as nodes and transactions
  as edges, creating network structures that reveal hidden
  relationships between wallets."
- PolymarketScan, Polywhaler, and PolyTrack all ship production
  wallet-cluster tooling against Polymarket specifically — signal
  that this is the differentiated, high-value section in the
  newsletter trilogy.

## Implementation status

| Component | Path | Status |
|-----------|------|--------|
| Funding tracer | `src/polymarket_insider_tracker/profiler/funding.py` | **Implemented** — follows USDC Transfer events backwards, max 3 hops, stops at known entity or root. |
| Entity registry | `src/polymarket_insider_tracker/profiler/entities.py` + `entity_data.py` | **Implemented** — hard-coded CEX hot wallets (Binance, Coinbase, Kraken), bridges (Stargate, Wormhole), Uniswap pools. |
| Storage | `funding_transfers` table + `FundingRepository` | **Implemented** — persists one row per traced transfer; indexed on `to_address`, `from_address`, `block_number`. |
| Unit tests | `tests/profiler/test_funding.py`, `test_entities.py`, `test_chain.py` | **Implemented** — ~70 tests covering hop limits, entity classification, RPC caching. |
| **Gap: cluster table** | `wallet_relationships` | Migration exists but **no writer is wired**. The Théo case calls for edges like `(wallet_a, wallet_b, "same_funding_origin", confidence=0.85)` so the weekly newsletter can report "5 of 7 alerted wallets cluster to the same origin within 3 hops". |
| **Gap: backwards-graph rollup** | | The per-wallet funding chain is stored, but the *graph inversion* (from-origin → subgraph of funded wallets) isn't. A single SQL view over `funding_transfers` would give it to us. |

## Ready-to-run test

```bash
cd /home/ami/AMI-AGENTS/projects/polymarket-insider-tracker
PATH=$AMI_ROOT/.boot-linux/bin:$PATH uv run pytest \
    tests/profiler/test_funding.py tests/profiler/test_entities.py \
    tests/profiler/test_chain.py -v
```

Query for the current state of the funding graph:

```sql
-- Every wallet that received USDC from the same CEX hot wallet
-- in the last 48h (Théo-style "common origin" candidate)
SELECT from_address AS origin, count(DISTINCT to_address) AS funded_count,
       sum(amount) AS total_usdc
  FROM funding_transfers
 WHERE timestamp >= now() - interval '48 hours'
   AND from_address IN (
         '0xf977814e90da44bfa03b6295a0616a897441acec'  -- Binance 20
         /* …entity_registry addresses… */
       )
 GROUP BY from_address
HAVING count(DISTINCT to_address) >= 3
 ORDER BY funded_count DESC;
```

## Next-step implementation path (1-2 days)

1. **Populate `wallet_relationships`** from funding-tracer output.
   Add a write in `FundingTracer.trace()` that inserts an
   (A, B, `"shared_origin"`, confidence) row whenever two target
   wallets resolve to the same 1- or 2-hop origin.
2. **`RelationshipRepository.clusters_for_origin(origin_address,
   days)`** — return the subgraph of wallets that funded from a
   given origin in the window.
3. **Weekly newsletter query** — top 10 origins by
   funded_wallet_count, with the list of member wallets each (at
   most 20 per cluster to keep the email readable).

## Newsletter mock (weekly)

```
Entity-linked clusters                            (week of 2026-04-13)

 1  Origin: Binance 20 hot wallet
     13 wallets funded in 48h · $1.1M aggregate notional
     Flagged behaviour: 8 bet on same 2 niche markets, all within
                        a 3-hour window.
     Sample member wallets:
        0xabc…1 (age 12h, nonce 2)
        0xabc…2 (age 8h,  nonce 1)
        …

 2  Origin: unknown contract 0xdef…456
     5 wallets funded in 72h · $240k aggregate
     … (same shape)

Weekly cluster count: 4 new, 2 grown (last week), 1 dormant.
```
