# Operator Runbook

This is the first-response doc when the live pipeline or the
newsletter cadences misbehave. Covers the four most common
failure modes; every section has a concrete "do next" list.

## Scenario test fails in CI

**First**: look at the failing test's output. Most failures fall
into one of three buckets:

### Bucket A — golden diff

`AssertionError: rendered output does not match golden at
fixtures/golden/<name>.html` with a unified diff in the output.

- **If the detector change was intentional** (new threshold, new
  template field): run
  `pytest tests/scenarios --update-snapshots`, review each changed
  file with `python scripts/review-snapshot.py <path>`, commit the
  updated golden + `.reviewed.yaml` in the same PR.
- **If the detector change was *not* intentional**: revert the
  change, investigate, re-open the PR.

### Bucket B — mutation-guard failure

`tests/scenarios/test_mutation_guard.py::test_*` failing means a
scenario is no longer asserting on what it claims to assert on.

- Inspect the mutation in the failing test — it nudges a specific
  threshold to prove the scenario cares about it.
- If the scenario "passes" because the mutation didn't change
  behaviour, the scenario's assertions are too loose. Tighten them.
- **Never** fix a mutation-guard failure by relaxing the guard.

### Bucket C — infra / flake

Timeouts, SQLite lock errors, himalaya download failures.

- Re-run the failed job first.
- If it fails twice, open an issue and tag `ci-infra`.
- For SQLite locks specifically: check if the scenario is holding
  an engine open across its full body — should be using the
  per-fixture `async_engine` and `async_sessionmaker` pattern
  documented in `tests/scenarios/conftest.py`.

## Live backtest sanity-band violation

CI nightly runs the backtest harness against the last 72h of
capture. If the `combined` precision sanity band (0.2 – 0.95) fails:

1. **Upper bound** (precision > 0.95): almost always synthetic
   inputs leaking into the `detector_metrics` table. Check for
   scenario fixtures being replayed against the production DB
   URL rather than the in-process SQLite.
2. **Lower bound** (precision < 0.2): detector regression.
   - Diff the last 7 days of `detector_metrics` rows for the
     problematic signal.
   - If the regression happened after a specific commit, bisect.
   - If the regression is gradual (e.g. wash-trading volume rising
     faster than insider-signal volume), tune the threshold and
     document the change in `docs/CLAIMS-AUDIT.md`.

## Newsletter sends degrade

### No subscribers in the delivery list

- Check `scripts/newsletters/_common.fetch_db_targets` against the
  `subscribers` table directly: `SELECT status, count(*) FROM
  subscribers GROUP BY status;`.
- If everybody is `pending_opt_in`, the `/opt-in` endpoint is
  either misconfigured or the confirmation-email sender is
  silently failing. Check the `email_deliveries` ledger for
  `outcome='failed'` rows.

### Empty-state newsletters land in inboxes

The Tera templates guard each section with
`{% if report.x | length > 0 %}`, so an empty day sends a mostly
blank email. If that's not what you want:

- Gate the send at the data-builder level: if all cadence
  aggregates are empty, return rc=0 without invoking himalaya.
- Add the guard before F.4 subscriber rollout so public addresses
  never see an empty send.

### Bounces not flipping subscribers

- Confirm `scripts/bounce-drain.py` is receiving the relay's DSN
  jsonl. `tail -f data/bounces/pending.jsonl` should tick with
  new rows on each bounce.
- Check `SubscribersRepository.record_bounce` threshold
  (`DEFAULT_BOUNCE_THRESHOLD = 3`). Three consecutive hard
  bounces are required before the status flips to `bounced`.
- If the threshold is right but the column isn't incrementing,
  the DSN's `bounce_type` field isn't being classified as `hard`.
  Check the sidecar that produces the jsonl — some relays use
  RFC 3463 enhanced status codes, others use free-form text.

## Detector threshold decision

When threshold reality shifts (e.g. Polymarket's median market
volume doubles and 2% volume impact becomes noise):

1. Run the backtest harness on the last 30 days of capture with
   the candidate threshold.
2. Update `docs/CLAIMS-AUDIT.md` with the new threshold + rationale
   + before/after precision/pnl_uplift numbers.
3. Update the threshold in `detector/*.py`, the matching docstring
   in `docs/newsletter-sections/*.md`, and the README if the
   threshold is cited there.
4. Run the full scenario suite with `--update-snapshots` where
   goldens change; review each via `scripts/review-snapshot.py`.

## Contacts and escalation

- Code owners for `detector/`: see `CODEOWNERS`.
- Newsletter delivery: `ami-reports@ami.local` (bounces from
  Gmail / Outlook go here).
- Sensitive secrets (Polymarket API, Cloudflare token): AMI-STREAMS
  ansible vault; operator runbook is
  `projects/AMI-STREAMS/ansible/roles/ami_mail/README.md` if you
  need to rotate.
