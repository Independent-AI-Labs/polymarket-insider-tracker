# Implementation TODOs — E2E scenario tests + validation pipeline

Every task required to get the four newsletter scenarios (fresh
wallets, unusual sizing, niche markets, funding clusters) written,
validated against synthetic inputs, validated against recorded live
captures, integrated into CI, and triple-checked for regressions.

Format: GitHub-style checklist with a section header per phase.
Each leaf task carries a one-line *Definition of Done* (DoD) and,
where relevant, an explicit dependency (`blocks`/`blocked-by`).

Numbering is `P.S.T` = Phase · Section · Task.

---

## Phase 1 — Harness foundations

### 1.1 Directory skeleton

- [x] **1.1.1** Create `tests/scenarios/` package with `__init__.py`.
  DoD: `pytest --collect-only tests/scenarios` returns no errors and
  zero tests.
- [x] **1.1.2** Create `tests/scenarios/fixtures/` with subdirs
  `inputs/`, `snapshots/`, `golden/`. Commit a `.gitkeep` in each.
- [x] **1.1.3** Create `tests/scenarios/conftest.py` importing the
  base `Base` metadata and exposing an async-SQLite engine fixture.
  DoD: `pytest tests/scenarios -q` collects the empty harness
  without error.

### 1.2 Scenario class

- [x] **1.2.1** Write `tests/scenarios/_harness.py::Scenario`
  dataclass-ish class with builder-style methods:
  - `given_trades(events: list[TradeEvent] | Path)` — materialises a
    tmp jsonl via `backtest.replay.trade_event_to_record`.
  - `with_wallet_snapshots(dict[str, WalletSnapshot])` — stores a
    dict; resolver closure looks up by address (default `is_fresh=False`).
  - `with_market_snapshots(dict[str, MarketSnapshot])` — same.
  - `with_funding_transfers(list[FundingTransferDTO])` — seeds
    `funding_transfers` table.
  DoD: unit test in `tests/scenarios/test_harness_internals.py`
  verifies each builder returns `self` and the resulting
  intermediate state is accessible.
- [x] **1.2.2** Add `Scenario.when_replayed() -> list[ReplayAssessment]`
  that invokes `backtest.replay_capture` with the stored resolvers.
  DoD: round-trips a single synthetic trade through the detector
  heuristics and asserts at least one assessment returned.
- [x] **1.2.3** Add `Scenario.and_rolled_up(day=date)` that consumes
  the assessments, emits fake alert-record payloads the way
  `alerter.history.AlertHistory.record` would, and invokes
  `scripts/compute-daily-rollup.py::_aggregate` directly.
  DoD: verify `alert_daily_rollup` rows present post-call.
- [x] **1.2.4** Add
  `Scenario.when_newsletter_built(cadence: str) -> dict` returning
  the YAML payload the cadence builder would emit. Internally
  imports the cadence script via `importlib` (same trick used by
  `test_send_report_data.py`).
  DoD: the returned dict matches the shape the Tera template
  consumes (`report.title`, `report.stats`, etc.).
- [x] **1.2.5** Add
  `Scenario.then_renders_html(substrings: list[str])` — writes the
  payload as a one-row YAML, invokes
  `himalaya batch send --dry-run --output json`, parses the JSON,
  asserts each substring is present in the rendered body. `skip`
  when `himalaya` not on PATH.
  DoD: happy-path scenario round-trips a known substring.
- [x] **1.2.6** Add
  `Scenario.then_matches_golden(path)` with a
  `--update-snapshots` flag (pytest option) that rewrites the
  golden file when set.
  DoD: a deliberately wrong golden fails the test; `-p
  no:cacheprovider --update-snapshots` overwrites it.

### 1.3 Determinism shims

- [x] **1.3.1** Add a `freeze_time(iso_str)` context manager /
  pytest fixture that patches `datetime.now` everywhere the
  pipeline touches it: `alerter.history`, `backtest.metrics`,
  `scripts/compute-daily-rollup`, `newsletter_common`. Use the
  `freezegun` library or a hand-rolled monkeypatch.
  DoD: two test runs with the same frozen timestamp produce
  byte-identical rendered HTML.
- [~] **1.3.2** *(deferred: scrubber handles UUIDs in goldens)* Add a `deterministic_uuid(seed)` factory; patch
  `uuid.uuid4` in `RiskAssessment.assessment_id` generation and
  anywhere else UUIDs leak into output.
  DoD: rendered HTML contains stable assessment-ids across runs.
- [x] **1.3.3** Normalise rendered-HTML output (strip volatile
  substrings: absolute paths, process IDs, localhost hostnames) in
  `Scenario.then_matches_golden` before the diff. Document the
  scrubbing regexes inline.
  DoD: harness-internals test demonstrates identical output after
  scrubbing for two runs in different working directories.

### 1.4 Himalaya binary gating

- [x] **1.4.1** Write a module-scoped `himalaya` fixture that
  `pytest.skip`s when `.boot-linux/bin/himalaya` is missing (mirror
  `tests/scripts/test_template_render.py`).
  DoD: scenario tests get skipped, not fail, on a fresh clone
  without the fork built.
- [x] **1.4.2** Cache a `himalaya --version` parse result per
  session; assert all required feature flags are present (`+batch`,
  `+template-vars`, `+send-block` for this test plane).
  DoD: missing feature flag produces a precise skip message.

### 1.5 Harness self-tests

- [x] **1.5.1** `tests/scenarios/test_harness_internals.py`: builder
  round-trip, idempotent `given_trades`, snapshot override.
- [x] **1.5.2** Cover the happy golden round-trip end-to-end with a
  trivial 1-wallet/1-market fixture (the "harness smoke").
- [x] **1.5.3** Run harness self-tests ×10 in a loop; verify zero
  flakes (`pytest --count=10 tests/scenarios/test_harness_internals.py`
  via `pytest-repeat`).
  DoD: 10/10 pass; add `pytest-repeat` to the `[dev]` extras.

---

## Phase 2 — Scenario 1: Fresh wallet (Maduro-style)

### 2.1 Fixture design

- [x] **2.1.1** `tests/scenarios/fixtures/inputs/fresh-wallet.jsonl`
  — 3 TradeEvents:
  - `hot` wallet (`0x7a3…f91`): nonce 2, age 2h, BUY YES @ 0.05,
    size 600000 → notional $30k, market slug
    `will-maduro-leave-office-by-end-of-jan-2026`.
  - `decoy-old`: 2-year-old wallet, nonce 500+, same market, $500.
  - `decoy-small`: fresh wallet but notional $200 (below threshold).
- [x] **2.1.2** `tests/scenarios/fixtures/snapshots/fresh-wallet.yaml`
  — per-address `WalletSnapshot` + per-market-id `MarketSnapshot`
  (`daily_volume=15000`, category `other` → niche_market also fires).
- [x] **2.1.3** Record the snapshot generation logic in an inline
  comment so the inputs are reproducible (name → address mapping is
  deterministic, not randomised).

### 2.2 Assertions

- [x] **2.2.1** Exactly 1 `fresh_wallet` assessment emitted;
  `decoy-small` dropped (below `min_trade_size`); `decoy-old`
  dropped (not fresh).
- [x] **2.2.2** `weighted_score ≥ 0.5` (base) and `≤ 1.0` (cap).
- [x] **2.2.3** Since the market is niche too, assert
  `signals_triggered == ("fresh_wallet", "niche_market")` and score
  reflects the ×1.2 multi-signal boost (≈ 0.78).
- [x] **2.2.4** `alert_daily_rollup` row present:
  `signal='fresh_wallet', unique_wallets=1, total_notional=30000`.
- [x] **2.2.5** Newsletter body contains:
  masked address, "age 2h", "nonce 2", "$30,000", market slug.

### 2.3 Golden file

- [x] **2.3.1** Capture first rendered HTML as
  `tests/scenarios/fixtures/golden/fresh-wallet-daily.html`.
- [~] **2.3.2** *(pending operator review)* Manual eyeball pass — operator reviews the golden
  and signs off that the output is what the newsletter *should*
  look like (checked in as `.golden-reviewed-by` comment at top of
  the file).

### 2.4 Negative controls

- [x] **2.4.1** Vary each threshold one at a time and confirm the
  assertion fails: nonce=10 → no alert; notional=$999 → no alert;
  age=72h → no alert.
- [x] **2.4.2** Property-based test (hypothesis): for
  `nonce ∈ [1..4] × notional ∈ [1000, 100000]` the detector fires;
  outside the region it doesn't.

---

## Phase 3 — Scenario 2: Unusual sizing (Iran-strike-style)

### 3.1 Fixture design

- [ ] **3.1.1** Input: one BUY NO trade at $48k against a
  daily-volume-$680k market (7% impact). Wallet `0xdef…789`, age
  18 months, nonce 500 → fresh_wallet suppressed.
- [ ] **3.1.2** Snapshots: `daily_volume=680000, book_depth=200000`.
  Book-impact = 48000/200000 = 24% → also triggers the
  `DEFAULT_BOOK_THRESHOLD=0.05` branch.
- [ ] **3.1.3** Add a second decoy trade at $10k against the same
  market (1.5% impact → below threshold).

### 3.2 Assertions

- [ ] **3.2.1** 1 `size_anomaly` assessment only; no `fresh_wallet`,
  no `niche_market` (volume > $50k).
- [ ] **3.2.2** Assessment metadata exposes both volume impact
  (~7.1%) and book impact (~24%).
- [ ] **3.2.3** `detector_metrics` row after aggregator run: 1
  alert, hits+misses+pending totals to 1.
- [ ] **3.2.4** Daily newsletter contains "7." (volume-impact %)
  and "48," (notional formatting) and the market question.

### 3.3 Golden file

- [ ] **3.3.1** `tests/scenarios/fixtures/golden/unusual-sizing-daily.html`,
  same operator-review gate as 2.3.2.

### 3.4 Negative controls

- [ ] **3.4.1** Mutate `daily_volume` up by 10× → no alert.
- [ ] **3.4.2** Omit `book_depth` → detector falls back to volume
  branch; alert still fires with volume-only reason string.
- [ ] **3.4.3** `daily_volume=None` AND `book_depth=None` → no
  alert (can't evaluate).

---

## Phase 4 — Scenario 3: Niche market (Google-search-style)

### 4.1 Fixture design

- [ ] **4.1.1** Input: 2 BUY trades at $2,500 and $4,200 into a
  market with `daily_volume=$25k, category='other'`.
- [ ] **4.1.2** Snapshots: deterministic; both wallets older than
  48h so fresh_wallet doesn't compound.

### 4.2 Assertions

- [ ] **4.2.1** Both trades emit `niche_market` + `size_anomaly`
  (each trade > 2% of $25k).
- [ ] **4.2.2** Composite weighted_score reflects the ×1.2
  multi-signal boost.
- [ ] **4.2.3** `alert_daily_rollup.signal='niche_market'` row:
  `alert_count=2, unique_wallets=2`.
- [ ] **4.2.4** **Weekly** newsletter (not daily) body contains the
  market id in the "Niche-market targeting" section — the first
  weekly-cadence assertion in the harness.
- [ ] **4.2.5** `Scenario.when_newsletter_built("weekly")` drives
  the weekly builder end-to-end, including the
  `top_markets_for_window` query against `alert_daily_rollup`.

### 4.3 Golden file

- [ ] **4.3.1** `tests/scenarios/fixtures/golden/niche-market-weekly.html`.

### 4.4 Negative controls

- [ ] **4.4.1** Bump `daily_volume` to $100k → `niche_market`
  dropped; assertion in the weekly template adapts (market not
  listed).
- [ ] **4.4.2** Category `politics` (not in
  `NICHE_PRONE_CATEGORIES`) at $25k volume: no `niche_market`
  alert; only `size_anomaly` fires.

---

## Phase 5 — `wallet_relationships` writer (blocks Scenario 4)

### 5.1 Design

- [ ] **5.1.1** Write `docs/newsletter-sections/04-funding-chains-design.md`
  (appendix) with the cluster-detection algorithm: "two wallets
  share origin" defined as "their 1- or 2-hop USDC funding path
  converges on the same `from_address` within a 48-hour window".
- [ ] **5.1.2** Confidence score formula: `0.5 + 0.15 × hop_overlap
  + 0.05 × simultaneity_bonus` capped at 0.95. Documented inline.
- [ ] **5.1.3** Decision: write relationships on every
  `FundingTracer.trace()` call? Too chatty → batched in the
  15-minute pipeline tick. Record the choice.

### 5.2 Implementation

- [ ] **5.2.1** Add `RelationshipRepository.upsert(dto)` with the
  portable insert-or-ignore (same pattern as
  `AlertRollupRepository.upsert`).
- [ ] **5.2.2** Add `FundingGraph` helper in
  `profiler/funding.py`:
  - `async def collect_shared_origins(wallets: list[str], window_hours=48) ->
      dict[str, list[str]]` returning origin → funded-wallets.
- [ ] **5.2.3** Wire `FundingGraph` into `Pipeline` as a 15-minute
  tick producer; each returned cluster writes N·(N−1)/2
  relationship rows with `type='shared_origin'`.
- [ ] **5.2.4** Add `RelationshipRepository.clusters_for_origin(origin, days)
  -> list[ClusterDTO]` query.

### 5.3 Tests

- [ ] **5.3.1** Unit tests for `collect_shared_origins` — hop
  overlap, time window boundary, entity-registered origin
  (Binance) treated specially.
- [ ] **5.3.2** Unit tests for `upsert` dedup: re-running against
  the same input doesn't duplicate rows.
- [ ] **5.3.3** Unit tests for `clusters_for_origin`: returns
  empty, singleton, multi-wallet clusters.

### 5.4 Migration / Back-compat

- [ ] **5.4.1** No migration needed (`wallet_relationships` table
  exists since the initial schema); confirm
  `alembic upgrade head` from a clean DB still works.
- [ ] **5.4.2** If the current pipeline tick doesn't exist, add
  a new `scripts/compute-funding-clusters.py` cron
  (`*/15 * * * *`) and document in README alongside the other
  cron recipes.

---

## Phase 6 — Scenario 4: Funding cluster (Théo-style)

### 6.1 Fixture design

- [ ] **6.1.1** 4 fresh wallets at varying ages (2h, 4h, 18h, 30h),
  each trading into the same 2 markets within a 3-hour window.
  Each wallet's notional > $1,000.
- [ ] **6.1.2** `funding_transfers` pre-seeded: all 4 wallets
  received USDC from `0xf977814e90da44bfa03b6295a0616a897441acec`
  (Binance 20 hot wallet) within 6 hours of each other.
- [ ] **6.1.3** Control: a 5th fresh wallet funded from an
  unrelated EOA (not a known entity) — should NOT appear in the
  cluster.

### 6.2 Assertions

- [ ] **6.2.1** All 4 real wallets fire `fresh_wallet`; the 5th
  does too but is excluded from the cluster.
- [ ] **6.2.2** `wallet_relationships` rows present: 6 pairwise
  `shared_origin` edges (4C2) with confidence in [0.5, 0.95].
- [ ] **6.2.3** `clusters_for_origin(binance20, 2)` returns exactly
  4 wallets.
- [ ] **6.2.4** Weekly newsletter HTML contains:
  `"4 wallets funded in 48h"`,
  `"Binance"` (rendered via `EntityRegistry.classify`),
  and the aggregate notional.

### 6.3 Golden file

- [ ] **6.3.1** `tests/scenarios/fixtures/golden/funding-cluster-weekly.html`.

### 6.4 Negative controls

- [ ] **6.4.1** Reduce cluster to 1 wallet → no cluster row in the
  newsletter.
- [ ] **6.4.2** Move transfers outside the 48h window → cluster
  rows dropped.
- [ ] **6.4.3** Replace Binance 20 with an unknown contract →
  cluster still detected (shared origin), but the newsletter label
  says "unknown contract" instead of "Binance".

---

## Phase 7 — Golden-file framework

### 7.1 Snapshot tooling

- [ ] **7.1.1** `--update-snapshots` CLI flag via
  `pytest_addoption` in `tests/scenarios/conftest.py`.
- [ ] **7.1.2** Normalisation pipeline documented — regex list in
  `_harness.py` with unit tests for each pattern.
- [ ] **7.1.3** Diff visualisation on failure — print first 30
  lines of `difflib.unified_diff(expected, actual)` with the path
  to the golden file for easy `cp actual golden` copy-paste during
  development.

### 7.2 Review workflow

- [ ] **7.2.1** `scripts/review-snapshot.py <golden-path>` —
  opens the HTML in `$BROWSER`, prints the diff vs. previous
  revision, prompts y/n, writes an approval note to
  `tests/scenarios/fixtures/golden/.reviewed.yaml`.
- [ ] **7.2.2** CI check that every golden under
  `fixtures/golden/` has an entry in `.reviewed.yaml` newer than
  the file's last git-commit timestamp. Fails otherwise.

### 7.3 Housekeeping

- [ ] **7.3.1** `.gitattributes` entry: `*.golden.html diff=html`
  so `git log -p` on the files is readable.

---

## Phase 8 — Mutation / adversarial validation

### 8.1 Mutation tests (does each scenario actually care about what it claims?)

- [ ] **8.1.1** For each scenario, generate N mutated copies with a
  single detector threshold nudged (e.g.
  `fresh_wallet.DEFAULT_MAX_NONCE = 4`); expect the corresponding
  scenario to fail. Catches "the scenario passed because nothing
  was being checked".
- [ ] **8.1.2** `tests/scenarios/test_mutation_guard.py` runs the
  full scenario suite against each mutation and asserts specific
  failures. Document which scenario cares about which threshold.

### 8.2 Adversarial fixtures

- [ ] **8.2.1** `fixtures/inputs/adversarial-nonce-5.jsonl`: wallet
  with nonce exactly 5 (boundary condition) — must NOT fire
  `fresh_wallet` (off-by-one guard).
- [ ] **8.2.2** `fixtures/inputs/adversarial-volume-exact-2pct.jsonl`:
  trade at exactly 2.0% of daily volume — must NOT fire
  `size_anomaly` (strict-greater-than semantics of the check at
  `detector/size_anomaly.py`; update the code or the doc if
  they disagree).
- [ ] **8.2.3** `fixtures/inputs/adversarial-sybil-dense.jsonl`:
  100 synthetic wallets from a single funding origin, 50 in the
  window and 50 outside. Cluster writer should emit exactly
  (50C2=1225) shared_origin edges, not 100C2.

### 8.3 Cross-scenario invariants

- [ ] **8.3.1** `tests/scenarios/test_isolation.py`: running
  Scenario 2 after Scenario 1 must not inherit any DB row from 1.
  (Shared harness state bug catcher.)
- [ ] **8.3.2** Randomised ordering via `pytest-randomly`; scenario
  suite must pass in any order.

### 8.4 Triple-check gates

- [ ] **8.4.1** **Gate 1 — synthetic**: all scenarios pass
  `pytest --count=5` (5 repeats each) with zero flakes.
- [ ] **8.4.2** **Gate 2 — mutation**: `test_mutation_guard.py`
  confirms each scenario fails when its corresponding threshold
  is nudged.
- [ ] **8.4.3** **Gate 3 — live capture** (Phase 9): scenario
  suite run against a real 72h capture passes ≥ 3 out of 4
  scenarios against *real* matching incidents (or documents why
  the live data produced 0 matches during the window).

---

## Phase 9 — Live capture + real backtest

### 9.1 Operational setup

- [ ] **9.1.1** Deploy `scripts/capture-trades.py` as a systemd
  user unit under `ami-cron @reboot` (per README). Document the
  journalctl query for health checks.
- [ ] **9.1.2** Configure the Polymarket API credentials
  (POLYMARKET_API_KEY/_SECRET/_PASSPHRASE) per the auth PR
  (`036b299`) so the capture tool has authenticated CLOB access.
- [ ] **9.1.3** Disk-space monitor — 100k-entry Redis stream at
  ~200 bytes/event ≈ 20 MB ceiling; daily jsonl captures will be
  larger. Log-rotate / archive policy: compress and move to
  cold storage > 30 days old.

### 9.2 First real backtest

- [ ] **9.2.1** After 72h of capture, run
  `python -m polymarket_insider_tracker.backtest.replay \
      --capture data/captures/capture-YYYYMMDD.jsonl`.
  DoD: non-zero `detector_metrics` rows written for each of
  fresh_wallet, size_anomaly, niche_market, combined.
- [ ] **9.2.2** Populate `tests/fixtures/insider-episodes.yaml`
  with concrete wallet/market pairs harvested from the capture —
  pick 3–5 from flagged alerts that match the shape of the README
  cases.
- [ ] **9.2.3** Add `outcomes.py` integration: for each metrics
  window, auto-fetch the market resolution from the Gamma API and
  label hit/miss/pending. Persist.
- [ ] **9.2.4** Define a sanity-band CI check: `combined` precision
  in `[0.2, 0.95]`; outside the band fails (upper bound catches
  synthetic-input leakage, lower bound catches detector
  regressions).

### 9.3 Real-data validation

- [ ] **9.3.1** Compare the 72h capture's fresh-wallet count
  against a known baseline (PolymarketScan public data for the
  same window) to within ±20%.
- [ ] **9.3.2** Cross-validate a named public case (e.g.
  AlphaRaccoon / 0xafEe from Section 3) against our detector: if
  the wallet appeared in the capture window, it must have been
  flagged.

---

## Phase 10 — CI integration

### 10.1 Workflow

- [ ] **10.1.1** `.github/workflows/ci.yml`: add a `scenarios` job
  matrix-parallel to the existing tests. Steps:
  1. Checkout
  2. Install Python, uv, deps
  3. Build himalaya fork (`bash projects/AMI-STREAMS/scripts/build-himalaya.sh`)
     OR download from a GitHub release artifact
  4. Run `uv run pytest tests/scenarios -q`
- [ ] **10.1.2** Cache the `.boot-linux/bin/himalaya` binary across
  workflow runs (actions/cache keyed on the submodule SHA).
- [ ] **10.1.3** Attach golden-HTML artifacts on failure so
  reviewers can diff locally.

### 10.2 Gate enforcement

- [ ] **10.2.1** Require all four scenario tests to pass for
  branch protection on `main`.
- [ ] **10.2.2** Forbid merges that change any
  `fixtures/golden/*.html` without updating `.reviewed.yaml`
  (the CI check from 7.2.2).

### 10.3 Feedback loops

- [ ] **10.3.1** Codecov upload — confirm scenario coverage
  touches every branch in
  `detector/fresh_wallet.py / size_anomaly.py / scorer.py`.
- [ ] **10.3.2** Nightly job that runs the scenario suite with
  `--update-snapshots`; opens a PR if any golden changed. Flushes
  out unintended template edits.

---

## Phase 11 — Documentation + runbook

### 11.1 Developer docs

- [ ] **11.1.1** `docs/scenario-tests.md`: how to add a new
  scenario (template, goldens, review workflow).
- [ ] **11.1.2** Update `README.md` "Running manually" section
  with scenario invocation examples.
- [ ] **11.1.3** Update `docs/newsletter-sections/*.md` to
  cross-link the relevant scenario test.

### 11.2 Operator runbook

- [ ] **11.2.1** `docs/RUNBOOK.md` — "what to do when a scenario
  fails in CI":
  - Is the detector threshold change intentional? If yes →
    `--update-snapshots` + operator review.
  - If no → revert the PR.
- [ ] **11.2.2** "What to do when the live backtest sanity-band
  fails": step-by-step incident response (freeze deploys, inspect
  recent detector changes, compare most-recent 3 captures).

### 11.3 Troubleshooting

- [ ] **11.3.1** Document `ImportError` on the himalaya binary
  feature check — point at the build script + the manifest's
  `minVersion` pin (SPEC §10).
- [ ] **11.3.2** Document SQLite vs Postgres datetime-tz drift
  (we hit it in Phase F.1; comment lives in
  `test_phase_f_repos.py::test_confirm_opt_in_is_idempotent`).

---

## Phase 12 — Final triple-check

This is the "don't ship it until" list.

- [ ] **12.1** Full `pytest -q` on `main` after Phase 11: 0
  failures, 0 unexpected skips.
- [ ] **12.2** Run each scenario 20× in a loop: 20/20 pass.
- [ ] **12.3** Mutation-guard suite (8.1.2): every scenario's
  corresponding detector mutation fails the expected scenario and
  no others (no cross-contamination).
- [ ] **12.4** Operator eyeballs every golden HTML at full width in
  a browser; no truncated content, no broken CSS, no leaking
  absolute paths.
- [ ] **12.5** Canary newsletter run against a private test
  mailbox via the real himalaya config (not `--dry-run`); mailbox
  receives all 3 cadence editions rendering cleanly. Mail-tester
  score ≥ 9/10.
- [ ] **12.6** Disable the backtest capture for 24h → weekly /
  monthly newsletters degrade gracefully (empty-state copy,
  documented in the Tera templates) rather than error.
- [ ] **12.7** All four newsletter sections from
  `docs/newsletter-sections/*.md` render in a single combined
  newsletter with no duplicated headers, no empty sections, and
  the wallet overlap tally across sections is internally
  consistent (a wallet flagged in Sec 1 + Sec 3 shows the same
  address in both).
- [ ] **12.8** Review
  `projects/AMI-STREAMS/docs/SPEC-MAIL.md` §§ 10 / 11 / 12 against
  what we actually ship — zero mismatches (no documented
  requirement without backing code; no feature without a
  requirement).
- [ ] **12.9** Security sweep: every subscriber-controlled value
  that lands in a Tera template is default-escaped; the only
  `| safe` is on `unsubscribe_url` and `observations` (which are
  sender-controlled). Documented in the scenario goldens by
  including an `X<script>Y` input and asserting it renders as
  `X&lt;script&gt;Y`.
- [ ] **12.10** Final pushed commit on each repo: CI green, all
  12 phases' DoDs satisfied.

---

## Cross-phase dependencies

```
1 ──┬──► 2 ──┐
    ├──► 3 ──┤
    ├──► 4 ──┼──► 7 ──► 8 ──► 10 ──► 12
    └──► 5 ──► 6 ──┘       9 ──────────┘
                           │
                       11 ◄┘
```

Phase 5 (wallet_relationships writer) is the one real dependency
chain — Scenario 4 can't assert against cluster rows until the
writer exists. Everything else runs in parallel.

## Effort rollup

| Phase | Developer-days |
|-------|----------------|
| 1 Harness | 1.5 |
| 2 Scenario 1 | 0.5 |
| 3 Scenario 2 | 0.5 |
| 4 Scenario 3 | 0.5 |
| 5 Writer | 1 |
| 6 Scenario 4 | 0.5 |
| 7 Golden framework | 0.5 |
| 8 Mutation / adversarial | 1 |
| 9 Live capture + real BT | operator-time (+ 0.5 dev to wire CI band) |
| 10 CI | 0.5 |
| 11 Docs | 0.5 |
| 12 Triple-check | 1 (sweep + canary + review) |
| **Total** | **~8.5 dev-days + live-capture window** |

## Out of scope (explicit non-todos)

- Any ML model training or adoption — out of scope until we have
  the detector_metrics baseline from Phase 9 to justify it.
- Event-correlation detector — roadmap-only per
  `docs/ROADMAP-EVENT-CORRELATION.md`.
- Multi-tenant subscriber sharding, A/B testing of newsletter
  copy, paid tiers — all out of scope.
- Polymarket order-placement automation — this repo is read-only.
