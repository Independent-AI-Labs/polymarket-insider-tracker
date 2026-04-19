"""Backtesting harness (Phase C).

Replays captured WebSocket trade streams through the live detector stack,
scores each resulting assessment against the market's eventual outcome,
and emits per-signal precision / PnL-uplift metrics that the monthly
calibration newsletter reads.

See `docs/CLAIMS-AUDIT.md` and the repo-side plan for context on what
this harness validates (notably the `$35K -> $442K` anecdote from the
README's Opportunity section).
"""

from polymarket_insider_tracker.backtest.metrics import (
    MetricsWindow,
    aggregate_metrics,
)
from polymarket_insider_tracker.backtest.outcomes import (
    AssessmentOutcome,
    MarketOutcome,
    OutcomeLabel,
    classify_assessment,
)
from polymarket_insider_tracker.backtest.replay import (
    ReplayAssessment,
    WalletProfileResolver,
    MarketMetadataResolver,
    replay_capture,
)

__all__ = [
    "AssessmentOutcome",
    "MarketOutcome",
    "MarketMetadataResolver",
    "MetricsWindow",
    "OutcomeLabel",
    "ReplayAssessment",
    "WalletProfileResolver",
    "aggregate_metrics",
    "classify_assessment",
    "replay_capture",
]
