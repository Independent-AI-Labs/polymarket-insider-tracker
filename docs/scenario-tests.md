# Scenario tests — developer guide

End-to-end tests under `tests/scenarios/` exercise the four
newsletter categories (fresh wallets, unusual sizing, niche markets,
funding clusters) through the full production code path: detector
stack → daily-rollup aggregator → newsletter data builder →
`himalaya batch send --dry-run`.

Harness: `tests/scenarios/_harness.py`. One `Scenario` class with
builder methods; fixtures + goldens under
`tests/scenarios/fixtures/{inputs,snapshots,golden}/`.

## Adding a new scenario

1. **Copy the skeleton from the fresh-wallet test**
   (`test_fresh_wallet_e2e.py`). The section-level docstring should
   cite the case it maps to in `docs/newsletter-sections/`.
2. **Build the fixture**: 2–4 trades covering the signal you're
   targeting plus at least one decoy whose behaviour documents the
   negative branch.
3. **Seed resolvers** via `with_wallet_snapshots` and
   `with_market_snapshots`. Defaults are `nonce=500 / is_fresh=False`
   for unresolved wallets and `None` metadata for unresolved markets
   — lean on those rather than seeding every test wallet.
4. **Assert in layers**: first on the assessment list (detector
   behaviour), then on `aggregate_rollup(day)` (storage shape), then
   on `render_newsletter(...)` (template output).
5. **Add negative controls**: for each assertion, add a variant that
   mutates one piece of state and confirms the corresponding signal
   goes silent. This is your mutation guard for free.
6. **Generate the golden** with `pytest --update-snapshots`. Review
   the resulting HTML in a browser via
   `uv run python scripts/review-snapshot.py <path>` — the tooling
   writes your approval to `.reviewed.yaml`.
7. **Run with pytest-randomly** (default) to confirm ordering
   independence. If the scenario depends on state from a prior
   test, you have a bug — not a test-order problem.

## Troubleshooting

### Test fails only in CI

- Check that the himalaya binary version matches the one pinned in
  `.github/workflows/ci.yml::vars.HIMALAYA_VERSION`. Locally you're
  rebuilding the `heads/ami` branch; CI downloads a release asset.
- Check the `scenarios` job logs for a "golden-review gate FAILED"
  line — that's `scripts/review-snapshot.py --check` blocking a
  golden change that wasn't approved.

### `TypeError: unsupported operand type(s) for -: 'datetime.datetime'`

Usually a timezone mix — scenarios run against SQLite, which
strips tz-info on readback. Normalise with
`.replace(tzinfo=None)` on both sides of the comparison, or
upgrade the assertion to delta-tolerance
(`abs(a - b) < timedelta(seconds=1)`).

### Scenario passes but shouldn't

Run the mutation-guard suite locally:

```bash
pytest tests/scenarios/test_mutation_guard.py -v
```

If the corresponding mutation for your signal still passes, the
scenario's assertion is too loose. Tighten it.
