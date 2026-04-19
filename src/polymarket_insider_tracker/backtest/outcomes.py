"""Classify each replay assessment against the market's eventual outcome.

Separate from `replay` so it can be unit-tested without any live detector
stack or network I/O.

A "hit" is determined by comparing the signaled side's share price at the
time of the assessment against the market's resolution (or a fallback
final-30-day price trajectory from the Gamma API).

- For a `BUY YES` alert with assessment price 0.2 and resolution YES (1.0),
  the signaled side moved by +0.8 (+8000 bps) — clear hit.
- For a `BUY YES` alert with assessment price 0.2 and resolution NO (0.0),
  the signaled side moved by −0.2 (−2000 bps) — clear miss.
- Markets that haven't resolved and haven't moved meaningfully are
  labelled `pending`.

Pure helpers — no DB, no HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class OutcomeLabel(StrEnum):
    """Label assigned to a replayed assessment."""

    HIT = "hit"
    MISS = "miss"
    PENDING = "pending"


# Bps delta that counts as a decisive move. 500 bps (5%) matches the
# weekly newsletter's "panned out" threshold and is the default the
# aggregator uses.
DEFAULT_MOVE_THRESHOLD_BPS = 500


@dataclass(frozen=True)
class MarketOutcome:
    """Snapshot of what actually happened in a market after an assessment.

    `resolved` carries the final settled price of the signaled outcome
    token (0.0 or 1.0 after resolution; intermediate value for
    unresolved markets). `reference_price` is the price at the moment
    of assessment — the denominator for the move calculation.
    """

    market_id: str
    reference_price: Decimal       # signaled outcome price at assessment time
    final_price: Decimal           # signaled outcome price at end of window
    is_resolved: bool              # market has settled (final_price ∈ {0, 1})


@dataclass(frozen=True)
class AssessmentOutcome:
    """Replay-time assessment paired with its classified outcome."""

    assessment_id: str
    wallet_address: str
    market_id: str
    side: str                      # BUY | SELL
    outcome_index: int             # 0 or 1
    reference_price: Decimal
    final_price: Decimal
    move_bps: int                  # (final - reference) × 10000, signed
    label: OutcomeLabel
    signals_triggered: tuple[str, ...]  # e.g. ("fresh_wallet", "size_anomaly")
    weighted_score: float


def classify_assessment(
    *,
    assessment_id: str,
    wallet_address: str,
    market_id: str,
    side: str,
    outcome_index: int,
    signals_triggered: tuple[str, ...],
    weighted_score: float,
    outcome: MarketOutcome,
    move_threshold_bps: int = DEFAULT_MOVE_THRESHOLD_BPS,
) -> AssessmentOutcome:
    """Classify a single assessment against the market outcome.

    Rules:
    - `hit` if the signaled side moved at least `move_threshold_bps` in
      the direction the trade bet on (BUY = up, SELL = down).
    - `miss` if the signaled side moved at least `move_threshold_bps` in
      the opposite direction.
    - `pending` if the market hasn't resolved and the absolute move is
      below threshold — we can't tell yet.
    """
    move = outcome.final_price - outcome.reference_price
    move_bps = int(move * Decimal(10000))
    abs_bps = abs(move_bps)

    label: OutcomeLabel
    if not outcome.is_resolved and abs_bps < move_threshold_bps:
        label = OutcomeLabel.PENDING
    else:
        favourable = move_bps >= move_threshold_bps if side == "BUY" else move_bps <= -move_threshold_bps
        adverse = move_bps <= -move_threshold_bps if side == "BUY" else move_bps >= move_threshold_bps
        if favourable:
            label = OutcomeLabel.HIT
        elif adverse:
            label = OutcomeLabel.MISS
        else:
            # Resolved but didn't move materially — treat as miss; the
            # alert didn't pay, which is the operator's relevant
            # perspective for precision.
            label = OutcomeLabel.MISS

    return AssessmentOutcome(
        assessment_id=assessment_id,
        wallet_address=wallet_address,
        market_id=market_id,
        side=side,
        outcome_index=outcome_index,
        reference_price=outcome.reference_price,
        final_price=outcome.final_price,
        move_bps=move_bps,
        label=label,
        signals_triggered=signals_triggered,
        weighted_score=weighted_score,
    )
