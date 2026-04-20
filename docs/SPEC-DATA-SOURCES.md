# SPEC-DATA-SOURCES

**Status:** draft
**Authors:** claude-operator + vlad
**Date:** 2026-04-20
**Parent:** `docs/SPEC-MARKET-SIGNALS.md`

Every signal in the `docs/signals/*.md` taxonomy pulls data from
one or more of the sources catalogued here. This doc is the
authoritative record of what's available, what it costs, what
limits apply, and what we've measured empirically.

Operators adding new signals must reconcile their data needs
against this catalog *before* coding.

---

## 1. Matrix

| Source | Transport | Latency | Wallets | History | Status | Cost |
|---|---|---|---|---|---|---|
| 1.1 `gamma-api.polymarket.com` | HTTPS REST | seconds | — | live only | ACTIVE | free |
| 1.2 `data-api.polymarket.com` | HTTPS REST | 2-5 s | yes | 3000-row cap | ACTIVE | free |
| 1.3 `clob.polymarket.com` | HTTPS REST | seconds | partial | live only | ACTIVE | free |
| 1.4 `ws-subscriptions-clob.polymarket.com/ws/market` | WSS | 100-200 ms | **no** | live only | ACTIVE | free |
| 1.5 `ws-live-data.polymarket.com` (activity feed) | WSS | 100-200 ms | yes | live only | BLOCKED¹ | free |
| 1.6 Polygon RPC (`polygon-rpc.com`) free tier | HTTPS JSON-RPC | seconds | yes | full chain | BLOCKED² | free |
| 1.7 Polygon RPC paid (Alchemy / Infura / QuickNode) | HTTPS + WSS | seconds | yes | full chain | REQUIRED for Tier 3 | $50-200/mo |
| 1.8 `entities.yaml` | local YAML | n/a | yes | n/a | ACTIVE | free |
| 1.9 `scheduled_events.yaml` | local YAML | n/a | — | n/a | OPERATOR-SEED | free |
| 1.10 News feed (NewsAPI / Bloomberg / X firehose) | HTTPS REST | seconds | — | varies | ROADMAP | $100-1000/mo |
| 1.11 Kalshi API | HTTPS REST | seconds | — | API-dependent | ROADMAP | varies |

¹ Upstream-DNS blocked in current deployment (resolved to
  127.0.0.1 via ISP). Pinned in `/etc/hosts` via
  `dns-probe-patch.py`, but Polymarket also migrated this hostname
  to what looks like a retired endpoint — trades don't arrive even
  with reachable DNS.
² `polygon-rpc.com` returns `"API key disabled, reason: tenant
  disabled"` — public tier is closed.

---

## 2. Per-source detail

### 2.1 gamma-api.polymarket.com

The canonical metadata / pricing API. Backs Polymarket's website.

**Endpoints used:**

- `GET /markets` — list markets with filters. Accepts
  `condition_ids` (repeatable), `active`, `closed`, `order`,
  `ascending`, `limit`, `startDate`, `endDate`.
- `GET /markets/{id}` — single market detail.
- `GET /events/{slug}` — event metadata; ties multiple outcome
  markets together.

**Useful fields on a /markets row:**

- `conditionId` (66-char hex)
- `question`, `slug`, `description`
- `volume24hr` (USDC), `volumeNum` (USDC all-time),
  `volume1wk`, `volume1mo`
- `liquidityClob` (USDC currently in the CLOB book)
- `bestBid`, `bestAsk`, `lastTradePrice`, `oneDayPriceChange`,
  `oneWeekPriceChange`
- `startDate`, `endDate` (ISO 8601)
- `active`, `closed`, `archived`
- `outcomes` (JSON-encoded string), `outcomePrices`,
  `clobTokenIds` (ERC-1155 token IDs)
- `category`, `tags`

**Rate behaviour:**

- Free endpoint. No documented rate limit.
- Measured empirically: 50 parallel requests succeed without
  throttling; sustained 10 req/s is safe.

**Caching:**

- Cloudflare edge serves responses. `/markets?limit=1000` cached
  ~ 10-30 s.
- Use `condition_ids` variants to bust cache when needed.

**Used by:** categories 03 (velocity), 04 (divergence), 05
(proximity), 06 (multi-outcome arithmetic). Primary metadata
source for every newsletter section.

### 2.2 data-api.polymarket.com

The trades-history API. **This is the source of `proxyWallet`**
that the CLOB WS channel doesn't expose.

**Endpoints used:**

- `GET /trades?limit={n}&offset={k}&filterAmount={usd}&market={cid}&user={address}&takerOnly={bool}&filterType={CASH|...}`
  — paginated trade log, newest-first.
- `GET /trades/{tx_hash}` (verify existence, not currently used).

**Useful fields per trade:**

- `proxyWallet` (42-char hex — THE KEY FIELD)
- `side` — `BUY` | `SELL`
- `price` (implied probability 0–1)
- `size` (shares = USDC since each binary share has max payoff $1)
- `conditionId`, `asset` (ERC-1155 token id), `outcome`,
  `outcomeIndex`
- `timestamp` (UNIX epoch seconds)
- `transactionHash` — dedupe key
- `title`, `slug`, `eventSlug`, `icon`
- `name`, `pseudonym`, `bio`, `profileImage` — Polymarket
  profile metadata when the wallet registered one
- `orderType` — `taker` | `maker` (used by signal 03-B)

**Hard limits (measured):**

- `limit` caps at `1000` per request (values above are silently
  clamped).
- `offset` caps at `3000`. Attempting higher returns
  `{"error": "max historical activity offset of 3000 exceeded"}`.
- Combined with `filterAmount=10000`: 3000 rows = ~24-36 h of
  size-meaningful history. At `filterAmount=1` (retail dust
  included), 3000 rows ≈ 2 minutes.

**Rate behaviour:**

- Cloudflare edge caches. `/trades?limit={n}` with identical
  params serves same response for minutes (measured
  `Age: 60-120 s` range).
- `filterAmount` bustable — each new `filterAmount` value cache-
  misses on first request.

**Geo-blocking:**

- Geo-restricted in US, UK, TR, FR, BE, SG, and others.
- Currently DNS-hijacked in the deployment; pinned via
  `/etc/hosts`.

**Used by:** category 01 (fresh-wallet, unusual-size,
funding-origin), category 02 (OFI, stealth clustering),
category 05-A (proximity accel). Primary trade source for every
signal that needs `proxyWallet`.

### 2.3 clob.polymarket.com

The CLOB (Central Limit Orderbook) REST API. Exposes order-book
state and private operations (via auth).

**Endpoints used (public, no auth):**

- `GET /book?market={condition_id}` — current L2 order book.
- `GET /markets/{condition_id}` — market + token detail.
- `GET /prices-history?market={token_id}&interval={day|hour|...}`
  — historical trade-price series.

**With auth (not currently used):**

- Place / cancel orders (requires API key + signing).

**Rate behaviour:**

- Documented 60 req / 10 s window.
- `py-clob-client` handles signing + rate-limit backoff.

**Used by:** category 03-C (book depth imbalance), 02-D
(price-impact asymmetry as fallback), and the
`clob_client.py` pipeline already wired for auth.

### 2.4 ws-subscriptions-clob.polymarket.com /ws/market

The CLOB WebSocket **market channel** — real-time price + book
updates. Current Tier-1 (hot path) source.

**Subscribe payload:**

```json
{"assets_ids": ["<token_id>", "..."], "type": "market"}
```

Flat object, NO `subscriptions` wrapper.

**Event types streamed:**

- `book` — full L2 snapshot (ships as a JSON array of per-asset
  dicts on subscribe).
- `price_change` — level delta.
- `last_trade_price` — trade execution on that asset. THE
  closest equivalent to a trade event; carries
  `asset_id, market, price, side, size, timestamp, fee_rate_bps`.

**Does NOT carry:** `proxyWallet`. The market channel is
anonymised. Wallet attribution requires Tier 2 (data-api) or
Tier 3 (on-chain).

**Used by:** Tier-1 hot-path alerting (real-time price + book
signals). NOT used for category 01 signals that need a wallet.

### 2.5 ws-live-data.polymarket.com (activity feed)

The legacy "activity" WebSocket. Used to carry trade frames with
`proxyWallet`.

**Status:** resolver-blocked in the deployment AND appears to
have been retired by Polymarket (trades don't arrive on the
subscribe `{"topic": "activity", "type": "trades"}` payload
even against real Cloudflare IPs). Documented in
`docs/WS-SUBSCRIPTION-BUG.md`.

**Do not re-use.** Consider this endpoint dead.

### 2.6 polygon-rpc.com (free tier)

The public free Polygon JSON-RPC endpoint. Used to exist. Now
returns `"tenant disabled"` on every call.

**Status:** BLOCKED at the tenant level. Not usable.

### 2.7 Polygon RPC (paid provider)

**Required for Tier 3** — the canonical on-chain indexer.
Candidates:

- **Alchemy** — `eth_subscribe newHeads` + `eth_getLogs`, 300M
  compute-units/mo on the Growth tier ($49/mo).
- **QuickNode** — similar. $49-$299/mo depending on throughput.
- **Infura** — Polygon endpoint available. $50/mo base.

**What it unlocks:**

- Wallet first-seen timestamp (category 01-A definitive variant).
- `funding_transfers` population (category 01-C).
- CTFExchange `OrderFilled` log stream (Tier 3 canonical trade
  source; see `docs/IMPLEMENTATION-TODOS.md` § 14.2).
- Arbitrary wallet history for context.

**Status:** REQUIRED for production Tier 3. Operator-level
procurement.

### 2.8 entities.yaml

Hand-curated registry. Schema in each signal doc that uses it
(01-C, 01-D).

**Current sections (planned):**

- `cex_deposits`: CEX deposit address prefixes (Binance,
  Coinbase, Kraken, OKX, Bybit, BitGet, KuCoin).
- `mixers`: Tornado Cash router contracts, Aztec, etc.
- `known_entities`: wallet → named entity lookups.
- `market_makers`: known MM wallets to exclude from informed-flow
  flagging.

**Maintenance cadence:** operator-weekly or on discovery.

### 2.9 scheduled_events.yaml

Hand-curated event calendar. Schema in 05-B.

**Current sections (planned):**

- Macro: Fed meetings, CPI, NFP, ECB, BoE releases.
- Political: election days, primary caucuses, debate dates.
- Legal: Supreme Court oral-argument / decision-release days.
- Sports majors: Super Bowl, World Series, Stanley Cup finals
  game dates.

**Maintenance cadence:** quarterly bulk refresh, weekly touch-up.

### 2.10 External news feed (roadmap)

See `docs/signals/05-event-catalyst.md` § 05-C. Not yet
integrated.

### 2.11 Kalshi / alternative-venue APIs (roadmap)

See `docs/signals/06-cross-market.md` § 06-D. Not yet
integrated.

---

## 3. Reachability from this deployment

Current deployment sits behind an ISP-level DNS hijack on
`*.polymarket.com`. `scripts/dns-probe-patch.py` detects the
condition and patches `/etc/hosts`. Probe runs on every
`make install` and should be re-run after network changes.

Hostnames currently pinned:

- clob.polymarket.com (104.18.34.205)
- ws-subscriptions-clob.polymarket.com (104.18.34.205)
- gamma-api.polymarket.com (104.18.34.205)
- ws-live-data.polymarket.com (104.18.34.205)
- data-api.polymarket.com (104.18.34.205)
- polymarket.com (64.239.109.1)
- www.polymarket.com (64.239.109.193)

SSL handshake succeeds against the Cloudflare edge via SNI —
the cert is correct because Cloudflare serves whichever vhost
matches the `Host` header.

---

## 4. Change log

- 2026-04-20 — initial catalog.
