# Newsletter review rubric — the advisor persona

**Purpose.** A repeatable review process that pre-flights every
newsletter render (daily / weekly / monthly) against the critical
eye of the target reader BEFORE it ships to a real subscriber list.
The reviewer is a persona: a seasoned financial advisor who follows
prediction markets as an alternative-data surface. Every iteration
loop runs the persona over the current render, logs findings, and
either fixes them or accepts them with rationale.

This is not a QA script for the infrastructure (that's the
scenario-test suite). It's a product-quality gate on the *content*.

---

## 1. The persona

**Name (shorthand):** "M."

**Who M. is.**

- 40-55 years old, 15-25 years in capital markets. CFA or equivalent.
- Currently runs a small discretionary book — family office, hedge
  fund analyst, or independent RIA. Own money and clients' money
  mixed.
- Treats Polymarket as an alternative-data feed: prediction-market
  prices are a real-time, probability-denominated news aggregator,
  often faster than options-implied probabilities for binary
  events (elections, regulatory decisions, geopolitical breakpoints).
- Numerate. Thinks in basis points, hazard rates, implied
  probabilities. Bayesian intuitions.
- Time-constrained. Has ~60 seconds for the daily, 5 minutes for
  the weekly, 20 for the monthly, and that's the *entire* attention
  budget — not per-section.

**What M. cares about.**

1. **Signal vs. noise.** Every row must clear the bar of "this
   tells me something I didn't already know from looking at the
   market ticker."
2. **Actionability.** What would M. *do* with this information?
   Size a position? Unwind one? Watch a market list? If the answer
   is nothing, the row is padding.
3. **Calibration.** M. internally keeps a model of how often our
   signals pan out. Overclaiming poisons that model permanently.
   The honesty notes aren't politeness — they're what keeps M.
   trusting the feed after three misses.
4. **Position context.** Is the flagged flow large relative to
   market depth? $50K into a $100K-liquidity market is a
   different animal than $50K into a $50M market.
5. **Catalyst density.** When does the market resolve? If soon,
   the signal is time-sensitive; if distant, it's positioning.
   M. wants that axis visible.
6. **Funding trail.** CEX-funded wallets behave differently from
   on-chain-only ones. That's a credible tell for M.'s book.
7. **Downside framing.** M. will not act on a one-sided claim.
   "This market moved 5 %" without "the last 10 such moves
   persisted / reverted at X %" is noise.

**What M. dismisses.**

- Naked precision claims without methodology ("our detector has
  87 % accuracy" — prove it).
- Jargon that substitutes confidence for content ("alpha",
  "insider-shape flow", "smart money") — M. has seen every one
  of these words used to hide thin analysis.
- Emoji, exclamation points, hype.
- Repetition across sections. If the top-wallet and the
  top-market sections name the same market, say it once.
- Attachments that duplicate the body. CSV is primary data for
  audit; if the email body is the CSV table copy-pasted, the
  email is redundant.
- Unsourced numbers. Every percentage, dollar figure, and count
  must walk back to a verifiable source.

**What flips M. from skeptic to subscriber.**

- Two calibrated, contrarian calls per quarter that moved the
  way the data said they would.
- Attribution: "we flagged X on day Y before Z happened" with
  the flag timestamp preserved in the archive.
- A disagreement: a month where the newsletter published a
  pattern that turned out to be a null. Owned publicly.

---

## 2. The review rubric

Applied to every rendered newsletter edition before send. Each
criterion is scored `✓` (pass), `~` (marginal — fix when
convenient), or `✗` (block — fix before shipping).

### 2.1 Universal

| # | Criterion | Pass condition |
|---|---|---|
| U1 | Headline carries the most striking fact in ≤ 25 words | Reader gets the "why open this?" in one sentence |
| U2 | No unsourced claim | Every number / name / percentage is either linked, attached as CSV, or explicitly derived in the body |
| U3 | No jargon substituting for content | "Alpha" / "insider" / "smart money" only when the underlying detector definition is linked |
| U4 | No precision/accuracy/hit-rate number before outcome scoring ships | Zero such claims anywhere in the body |
| U5 | Honesty note present when a section uses degraded data | e.g. live-feed fallback explicitly labelled; no detector-rollup language when rollup is empty |
| U6 | All external links click to a live destination | Wallet profile + market event pages return 200 |
| U7 | Attachments add something the body doesn't already show | CSV rows > body rows; PDF contains context email doesn't |
| U8 | Footer carries: legal name, postal address, reason-for-receipt, one-click unsubscribe | All four present, not lorem-ipsum |
| U9 | Body under the Gmail 102 KB clip threshold (daily + weekly) | Monthly MAY exceed; relies on PDF |
| U10 | Same fact doesn't appear in two sections | e.g. top market in wallets-table AND markets-table called out once, deep-linked once |

### 2.2 Daily-specific

| # | Criterion | Pass condition |
|---|---|---|
| D1 | Headline names ONE market as heaviest flow | Not a list of 3, not a statistic alone |
| D2 | Wallet rows show side (BUY/SELL) AND notional | Direction + size, not just wallet list |
| D3 | Market rows show close-date or days-to-resolution when available | Catalyst-density axis visible |
| D4 | Funding-origin section absent rather than fake | If `funding_transfers` empty, section doesn't render |
| D5 | Time window explicit and honest | If the live-feed window is 40 s, it says 40 s, not "24 h" |
| D6 | No "alpha" or "insider" language when source = data-api-live | Heading reads "most active", not "alpha" |

### 2.3 Weekly-specific

| # | Criterion | Pass condition |
|---|---|---|
| W1 | Wallet-of-the-Week profile names a specific wallet and walks funding chain | Not "top wallets had X pattern" — one wallet, one story |
| W2 | "Aged callouts" section frames price movement, not precision | "The 8 markets we flagged last Monday moved an average of +3.2 % / −1.1 % within the week" |
| W3 | Clusters listed are NEW this week | Not a full-history dump |
| W4 | "Stories in motion" markets are still open | Resolved markets get their own "closed out" micro-section or are dropped |

### 2.4 Monthly-specific

| # | Criterion | Pass condition |
|---|---|---|
| M1 | "Persistent operators" table: appearances ≥ 5 on daily watchlist | Threshold visible so reader understands the bar |
| M2 | Cluster graph is deterministic (seeded layout) | Same month → same image across re-renders |
| M3 | Signal mechanics section carries the "observations, not precision" note verbatim | Copy stable across editions |
| M4 | "What the ledger will add next month" section is not a marketing stub | Concrete: names the capability coming + its gating dependency |

---

## 3. The process

### 3.1 Pre-flight for every canary send

1. Run `make newsletter-daily --no-send` (or the equivalent for
   the cadence). Capture the rendered HTML + CSV + headline log
   line.
2. Pour the render into the advisor persona below.
3. Fill the **Review log** table (§ 4) — one row per criterion
   per edition.
4. Any `✗` is a block — fix in the builder or template, re-render.
5. Any `~` is a tracked TODO in the relevant phase of
   `IMPLEMENTATION-TODOS.md`, allowed to ship with a note.
6. `✓` across the board → canary send authorised.

### 3.2 Persona-rendering prompt

When running the review, render the text file of the email body
(or open the send in Gmail) and literally ask:

> *"I am M. I have sixty seconds before my morning call. I've
> just opened this newsletter. In thirty seconds, can I tell you
> the single most useful thing it said? Would I do anything
> about it? If you took away the headline, would the rest of
> the page pass the smell test of someone who has spent twenty
> years reading equity-research daily briefs?"*

Write the answer down. If the answer is "no" at any step,
identify which section killed it and patch.

### 3.3 Feedback incorporation

Every iteration loop produces:

- **Content deltas** → Tera template + data builder changes.
- **Spec deltas** → SPEC-NEWSLETTERS-POLYMARKET updates in the
  audit log (§ 14).
- **Rubric deltas** → this file, § 2. When we discover a new
  failure mode the rubric didn't catch, we add a criterion.

### 3.4 When to unsubscribe M.

The persona is a standing check. We stop consulting M. and ship
to real subscribers only when:

- Three consecutive canary editions score straight `✓` across
  every applicable criterion.
- An unaffiliated second reviewer confirms the last of those
  three independently.
- `docs/SPEC-NEWSLETTERS-POLYMARKET.md` § 10.6 canary checklist
  has at least seven consecutive clean days.

---

## 4. Review log

One block per canary edition. Append below; never rewrite prior
entries.

### Edition: `daily-2026-04-20` — live data-api source

Rendered: `make newsletter-daily` at 2026-04-20 09:00 UTC.

| # | Criterion | Score | Notes |
|---|---|---|---|
| U1 | Striking fact in headline | | |
| U2 | No unsourced claim | | |
| U3 | No jargon substituting for content | | |
| U4 | No precision claim | | |
| U5 | Honesty note present | | |
| U6 | Links click live | | |
| U7 | Attachments add value | | |
| U8 | Footer complete | | |
| U9 | Body < 102 KB | | |
| U10 | No fact repeated | | |
| D1 | Headline names ONE market | | |
| D2 | Wallet rows show side + notional | | |
| D3 | Market rows show close-date | | |
| D4 | Funding-origin absent rather than fake | | |
| D5 | Time window explicit | | |
| D6 | No "alpha"/"insider" on live feed | | |

**M.'s 30-second summary:** *(to fill in)*

**Blocking fixes:** *(to fill in)*

**Marginal findings:** *(to fill in)*

---

## 5. Out of scope for the rubric

- Engineering-quality checks (test coverage, lint, performance).
  Those belong in CI.
- Legal review of disclaimer language. Separate process.
- Translation / localisation. English only for now.
