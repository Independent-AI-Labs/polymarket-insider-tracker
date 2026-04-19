# Roadmap — Event Correlation

**Status:** NOT IMPLEMENTED (placeholder for Phase D.4 in the
approved plan at `/home/ami/.claude/plans/mellow-enchanting-melody.md`).

## What the README used to claim

The original README listed "Event Correlation" as one of the detection
algorithms:

> Cross-references trading activity with news feeds.
> Detects positions opened 1–4 hours before related news breaks.

This is aspirational — there is no news ingestor, no event-timestamp
matcher, no correlation detector in the repo. The claim has been
demoted to the README's `## Roadmap` section and tracked here.

## What this would require

1. **News ingestor** — a streaming subscriber to one or more feeds:
   - RSS aggregators (Google News, Bing News, NewsAPI).
   - Twitter/X timelines for well-known "scoop" accounts (rate-limited,
     requires a developer token).
   - Press-release wires (PR Newswire, Business Wire) for scheduled
     announcements.
   - Polymarket-specific sources (market-maker blogs, Discord feeds).
2. **Event normaliser** — NER / topic-classification pipeline that
   extracts (entity, event_type, timestamp, confidence) tuples from the
   raw feed items. Probably a light spaCy + LLM hybrid to avoid
   hallucinating events from noise.
3. **Market ↔ event matcher** — maps `TradeEvent.event_slug` /
   market question text to extracted event tuples. Lexical matching
   alone is lossy; embedding-based similarity (MiniLM or similar)
   is probably the floor.
4. **Correlation detector** — for each `TradeEvent` at time `t`, query
   the normaliser's store for matching events in the window
   `(t, t + 4h)`. If found, emit an `EventCorrelationSignal` with
   the delta and event metadata.
5. **Signal wiring** — `RiskScorer.DEFAULT_WEIGHTS` gains an
   `event_correlation` entry; the alert footer gets a
   "Signal triggered: positions opened Nm before news X" line.
6. **Observability** — event_correlation metrics flow through the
   Phase C backtesting harness (`backtest/metrics.py`), giving us
   per-signal precision / pnl-uplift so we can measure the false-positive
   rate from the news ingestor on a rolling basis.

## Why it is deferred

- Sourcing and deduplicating news feeds at production quality is its
  own project. The current detector stack (fresh-wallet + size-anomaly
  + niche-market) is still improving; validating those via Phase C's
  backtesting first will tell us whether Event Correlation is the
  right next signal to add, versus say ML-based sizing or graph-based
  entity detection.
- The README-cited statistic ("positions opened 1-4 hours before
  related news breaks") has not been validated against the current
  detector stack. Phase C's backtest runs against the seeded ground
  truth in `tests/fixtures/insider-episodes.yaml` will produce the
  evidence we need to prioritise this properly.

## Entry points when we pick it up

- `src/polymarket_insider_tracker/detector/event_correlation.py` —
  new module following the shape of `size_anomaly.py`:
  `EventCorrelationDetector.analyze(trade, *, news_events)`.
- `src/polymarket_insider_tracker/ingestor/news_feed.py` — streaming
  subscriber; probably a separate systemd unit like
  `capture-trades.py`.
- `alembic/versions/<new>_news_events.py` — persistent store of
  normalised events keyed by event_hash, with indices on timestamp
  and entity.
- `src/polymarket_insider_tracker/backtest/outcomes.py` — extend
  classifier to read the correlation window and label
  event-correlation-only signals.

## Non-goals

- Reinventing a full "alternative-data" market intelligence product.
  Stay scoped to signal quality for the insider-tracker use case.
- Trading on news events directly. The tool watches for suspicious
  wallets; it does not form opinions on whether news is accurate.
