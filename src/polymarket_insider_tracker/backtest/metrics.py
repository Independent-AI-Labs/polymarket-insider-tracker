"""Aggregate classified outcomes into one DetectorMetricsDTO per signal.

Pure helper — consumes the list of `AssessmentOutcome` objects produced
by `classify_assessment` and emits metrics rows ready for insertion via
`DetectorMetricsRepository.insert`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from polymarket_insider_tracker.backtest.outcomes import (
    AssessmentOutcome,
    OutcomeLabel,
)
from polymarket_insider_tracker.storage.repos import DetectorMetricsDTO


@dataclass(frozen=True)
class MetricsWindow:
    """Time window the aggregated rows apply to."""

    start: datetime
    end: datetime


# Signal keys the detector stack emits; aggregate_metrics emits one row
# per key plus a "combined" row counting any-signal-triggered assessments.
KNOWN_SIGNALS: tuple[str, ...] = ("fresh_wallet", "size_anomaly", "niche_market")
COMBINED_SIGNAL = "combined"


def _precision(hits: int, misses: int) -> Decimal | None:
    """Precision = hits / (hits + misses); None if the denominator is 0."""
    denom = hits + misses
    if denom == 0:
        return None
    return (Decimal(hits) / Decimal(denom)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def _pnl_uplift_bps(outcomes: Iterable[AssessmentOutcome]) -> int | None:
    """Average signed `move_bps` across non-pending outcomes.

    A positive number means the signaled side moved favourably on
    average; a negative number means it moved against the alert.
    Non-pending only — pending outcomes are unresolved noise.
    """
    scored = [o.move_bps for o in outcomes if o.label != OutcomeLabel.PENDING]
    if not scored:
        return None
    return sum(scored) // len(scored)


def aggregate_metrics(
    outcomes: Iterable[AssessmentOutcome],
    window: MetricsWindow,
) -> list[DetectorMetricsDTO]:
    """Produce one metrics row per signal (plus a combined row).

    The `combined` row counts assessments that fired >=1 signal — it's
    the precision/recall operators actually care about because it
    represents the alerts that would have been dispatched.
    """
    outcomes_list = list(outcomes)

    rows: list[DetectorMetricsDTO] = []

    def _emit(signal: str, scoped: list[AssessmentOutcome]) -> None:
        hits = sum(1 for o in scoped if o.label == OutcomeLabel.HIT)
        misses = sum(1 for o in scoped if o.label == OutcomeLabel.MISS)
        pending = sum(1 for o in scoped if o.label == OutcomeLabel.PENDING)
        rows.append(
            DetectorMetricsDTO(
                window_start=window.start,
                window_end=window.end,
                signal=signal,
                alerts_total=len(scoped),
                hits=hits,
                misses=misses,
                pending=pending,
                precision=_precision(hits, misses),
                pnl_uplift_bps=_pnl_uplift_bps(scoped),
            )
        )

    for sig in KNOWN_SIGNALS:
        scoped = [o for o in outcomes_list if sig in o.signals_triggered]
        _emit(sig, scoped)

    combined = [o for o in outcomes_list if o.signals_triggered]
    _emit(COMBINED_SIGNAL, combined)

    return rows
