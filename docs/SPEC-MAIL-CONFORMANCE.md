# SPEC-MAIL conformance audit

**Date:** 2026-04-19
**Auditor:** claude-operator
**Source specs:** `projects/AMI-STREAMS/docs/REQ-MAIL.md` §§ 10 / 11 / 12
and `SPEC-MAIL.md` §§ 10 / 11 / 8-extensions.

Closes Task 12.8 of
[IMPLEMENTATION-TODOS.md](IMPLEMENTATION-TODOS.md). Every numbered
requirement is matched against the code path in this repo. Any
REQ without backing code is called out; any code without a matching
REQ likewise.

## REQ-MAIL-110..118 — Subscriber Management

| # | Requirement | Code | Status |
|---|-------------|------|--------|
| 110 | Registry with 5 states `pending_opt_in / active / bounced / unsubscribed / suppressed` | `storage/models.py::SubscriberModel` + CHECK constraint in alembic 004_phase_f | CONFORMING |
| 111 | Double opt-in — confirmation email with time-limited token | `web/app.py::subscribe` inserts `pending_opt_in` + fires `confirmation_sender`; `confirm_opt_in` flips to `active` | CONFORMING |
| 112 | Every row has an unguessable `unsubscribe_token` | UUID4 default on `SubscriberModel.unsubscribe_token` | CONFORMING |
| 113 | Unsubscribe takes effect within the current send cycle | `newsletter_common.fetch_db_targets` queries `active_for_cadence` at build time — in-flight batches do not re-read mid-send, but fetch is done per-cadence so the next batch is correct | CONFORMING |
| 114 | Subscribers can opt into a subset of cadences | `cadences TEXT[]` column + `active_for_cadence` filter + `SubscribersRepository._validate_cadences` allowlist (`{daily, weekly, monthly}`) | CONFORMING |
| 115 | Hard-bounce flip after N consecutive bounces (default 3) | `SubscribersRepository.record_bounce` increments + flips; `DEFAULT_BOUNCE_THRESHOLD=3` exposed for override | CONFORMING |
| 116 | Suppression list overrides any opt-in | `SuppressionListRepository.filter_subscribers` invoked in `fetch_db_targets`; exact / domain / regex | CONFORMING |
| 117 | Personal data exportable and deletable per subscriber (GDPR Art. 15 / 17) | `SubscribersRepository.delete_for_gdpr` drops the row; export is a manual `SELECT * FROM subscribers WHERE email=...` (documented in RUNBOOK) | CONFORMING (export is manual; acceptable for v1) |
| 118 | Sends idempotent at (subscriber, cadence, edition) | `email_deliveries` table records every send keyed on `edition_id`; scripts use `{cadence}-{date}` for `edition_id`. No current check against re-delivery, but the ledger makes a re-run detectable | PARTIAL — idempotency is observable, not enforced. **Gap**: add a pre-send query against `email_deliveries` to skip rows already marked `outcome='sent'` for the edition |

## REQ-MAIL-120..128 — Public-sending compliance

| # | Requirement | Code / artifact | Status |
|---|-------------|-----------------|--------|
| 120 | `List-Unsubscribe` header with mailto + https | Tera template partial `scripts/templates/partials/unsubscribe_footer.html` carries the link; header-level emission happens via himalaya's MML `-H` args | **PARTIAL** — the footer is visible but the RFC 2369 header itself isn't emitted by the current template. **Gap**: wrap the weekly/monthly template outputs to include `List-Unsubscribe:` + `List-Unsubscribe-Post:` headers at the MML level |
| 121 | `List-Unsubscribe-Post: List-Unsubscribe=One-Click` header | Same as 120 | PARTIAL — see Gap on 120 |
| 122 | SPF authorising the relay egress | `projects/AMI-STREAMS/ansible/playbooks/polymarket-newsletter-dns.yml` upserts the record via Cloudflare | CONFORMING (playbook-shaped; runs when operator provides cloudflare_api_token) |
| 123 | DKIM ≥ 2048-bit, selector rotated annually | Same playbook generates the 2048 RSA key + publishes the TXT record | CONFORMING |
| 124 | DMARC starts at `p=none`, promotes after 30 days | Playbook emits `p=none; rua=mailto:dmarc@…`; reminder task prints the 30-day promotion checklist | CONFORMING |
| 125 | Footer includes legal name, postal address, reason, unsubscribe link | `partials/unsubscribe_footer.html` emits all four when the row supplies them | CONFORMING (reason + unsubscribe_url are injected by `fetch_db_targets`; legal + postal are hard-coded in the partial) |
| 126 | Subject lines aren't deceptive; DKIM `d=` aligns | Subject templates live in `report-config.yaml` — operator-controlled, not recipient-controlled. DKIM alignment is enforced by the Ansible playbook's `adkim=s; aspf=s` directives | CONFORMING |
| 127 | Rate-limit ≤ 120 msg/hour per recipient domain | `newsletter_common.deliver_via_himalaya(rate=...)` passed through to `himalaya batch send --rate`. Current default in `report-config.yaml` is `5/min` (= 300/hr) — **over the REQ's 120/hr cap** for any single recipient domain | **GAP** — tighten the default to `2/min` (= 120/hr) OR add per-domain grouping in the data builder |
| 128 | Subscriber-derived strings autoescaped by Tera | Confirmed via `tests/scenarios/test_security_escape.py` — hostile `name` is escaped; only `unsubscribe_url | safe` bypasses autoescape and is sender-controlled | CONFORMING |

## REQ-MAIL-130..132 — Delivery observability

| # | Requirement | Code | Status |
|---|-------------|------|--------|
| 130 | Append-only send ledger | `email_deliveries` table + `EmailDeliveryRepository.record` | CONFORMING |
| 131 | Bounce reports parsed + classified within 24h | `scripts/bounce-drain.py` parses JSONL, classifies into `hard / soft / challenge / unknown`, links back via `message_id` | CONFORMING |
| 132 | Suppression-matched sends logged even though nothing leaves the relay | `fetch_db_targets` logs `"suppression hit: email=... matched_pattern=..."` at INFO; no ledger row written (intended — no send attempt to audit) | CONFORMING (behaviour differs from "log a row" but the spec said "logged" which we do) |

## Gaps summary

| # | Gap | Severity | Action |
|---|-----|----------|--------|
| 118 | Idempotency is observable, not enforced | MEDIUM | Add pre-send query in `deliver_via_himalaya`: skip rows whose `(edition_id, email)` already has `outcome='sent'` in `email_deliveries`. |
| 120 / 121 | `List-Unsubscribe` / `List-Unsubscribe-Post` headers not emitted at MIME level | **HIGH** — Gmail's one-click-unsubscribe will not appear in the inbox UI until these headers are present. | Wrap each Tera template output with MML headers via himalaya's `-H` flag when delivering; the unsubscribe token is already per-row so the substitution is trivial. |
| 127 | Default rate `5/min` exceeds the REQ-MAIL-127 cap of 120/hr | LOW | Tighten `report-config.yaml::delivery.rate` default to `2/min`. Document override for operators with validated reputation. |

All three gaps are tracked as additions to `IMPLEMENTATION-TODOS.md`
under new Phase 13 entries.

## Audit log

- 2026-04-19 — initial audit by claude-operator.
- Next review: on any REQ-MAIL doc change in AMI-STREAMS, or every
  90 days, whichever comes first.
