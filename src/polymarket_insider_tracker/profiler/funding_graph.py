"""Shared-origin funding-cluster detection (Phase 5 of IMPLEMENTATION-TODOS).

Reads `funding_transfers` rows and groups wallets that received USDC
from the same origin within a 48-hour window. Groups ≥ 2 wallets
become `shared_origin` edges in `wallet_relationships`, consumed by
the weekly newsletter's "Entity-linked clusters" section.

Design doc
----------

See `docs/newsletter-sections/04-funding-chains.md`. The `Theo` case
(Chainalysis clustering of 11 Polymarket accounts into one trader)
is the motivating example — coordinated wallets consolidate on a
common CEX hot wallet in a tight time window.

Confidence formula (documented in IMPLEMENTATION-TODOS §5.1.2)

    0.5                                   # base
      + 0.15 × hop_overlap                 # |cluster| − 2, capped at 2
      + 0.05 × simultaneity_bonus          # 1 − window_used / window_max
    capped at 0.95

The cap leaves room for higher-confidence signals to come in later
(e.g. behavioural similarity, shared market targeting).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polymarket_insider_tracker.storage.models import FundingTransferModel
from polymarket_insider_tracker.storage.repos import (
    RelationshipRepository,
    WalletRelationshipDTO,
)


DEFAULT_WINDOW_HOURS = 48
DEFAULT_MIN_CLUSTER_SIZE = 2
MAX_WINDOW_SECONDS = DEFAULT_WINDOW_HOURS * 3600
RELATIONSHIP_TYPE = "shared_origin"


@dataclass(frozen=True)
class SharedOriginCluster:
    """A group of wallets funded from the same origin inside the window."""

    origin_address: str
    wallet_addresses: tuple[str, ...]
    earliest_transfer_at: datetime
    latest_transfer_at: datetime
    total_amount: Decimal

    @property
    def size(self) -> int:
        return len(self.wallet_addresses)

    @property
    def window_seconds(self) -> float:
        return (
            self.latest_transfer_at - self.earliest_transfer_at
        ).total_seconds()


async def collect_shared_origins(
    session: AsyncSession,
    wallet_addresses: Iterable[str],
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
) -> list[SharedOriginCluster]:
    """Group `wallet_addresses` by the origin that funded them.

    For each (origin, wallet) pair, the *earliest* transfer's timestamp
    anchors the window — subsequent transfers from the same origin to
    other wallets join the cluster if they land within `window_hours`
    of that anchor.

    Wallets with no funding row are silently dropped. Clusters below
    `min_cluster_size` are dropped.
    """
    targets = [w.lower() for w in wallet_addresses]
    if not targets:
        return []

    # Fetch every incoming transfer for the target set in one query.
    # SQLAlchemy's `.in_()` takes a concrete list — an empty list would
    # short-circuit, which we handled above.
    stmt = (
        select(FundingTransferModel)
        .where(FundingTransferModel.to_address.in_(targets))
        .order_by(FundingTransferModel.timestamp)
    )
    result = await session.execute(stmt)
    transfers = list(result.scalars().all())

    # Map: (origin, target) → earliest transfer. Later duplicates are
    # discarded because the earliest one defines when the funding
    # landed.
    earliest: dict[tuple[str, str], FundingTransferModel] = {}
    for t in transfers:
        key = (t.from_address, t.to_address)
        if key not in earliest:
            earliest[key] = t

    # Group by origin.
    by_origin: dict[str, list[FundingTransferModel]] = {}
    for (origin, _), t in earliest.items():
        by_origin.setdefault(origin, []).append(t)

    clusters: list[SharedOriginCluster] = []
    for origin, group in by_origin.items():
        if len(group) < min_cluster_size:
            continue
        group_sorted = sorted(group, key=lambda x: x.timestamp)
        # Windowing: slide a window_hours mask across the sorted
        # transfers and keep the largest sub-cluster contained in it.
        best: list[FundingTransferModel] = []
        window = timedelta(hours=window_hours)
        left = 0
        for right in range(len(group_sorted)):
            while (
                group_sorted[right].timestamp
                - group_sorted[left].timestamp
            ) > window:
                left += 1
            candidate = group_sorted[left : right + 1]
            if len(candidate) > len(best):
                best = list(candidate)
        if len(best) < min_cluster_size:
            continue
        wallets = tuple(sorted({t.to_address for t in best}))
        if len(wallets) < min_cluster_size:
            # Multiple transfers to the same wallet → dedup dropped
            # the cluster below threshold. Skip.
            continue
        total = sum((t.amount for t in best), Decimal("0"))
        clusters.append(
            SharedOriginCluster(
                origin_address=origin,
                wallet_addresses=wallets,
                earliest_transfer_at=best[0].timestamp,
                latest_transfer_at=best[-1].timestamp,
                total_amount=total,
            )
        )

    return clusters


def cluster_confidence(cluster: SharedOriginCluster) -> float:
    """Score a cluster per the formula documented at module top.

    Capped at 0.95 so future behavioural evidence can surface as the
    higher-confidence tier.
    """
    base = 0.5
    hop_overlap = min(cluster.size - 2, 2)  # cap at 2 extra hops
    hop_term = 0.15 * max(hop_overlap, 0)
    simultaneity = 1.0 - min(
        cluster.window_seconds / MAX_WINDOW_SECONDS, 1.0
    )
    simultaneity_term = 0.05 * simultaneity
    return min(base + hop_term + simultaneity_term, 0.95)


async def persist_clusters(
    session: AsyncSession,
    clusters: Iterable[SharedOriginCluster],
) -> int:
    """Write one `shared_origin` edge per unordered wallet pair.

    Returns the number of edges emitted (before dedup). Re-running
    against the same input is a no-op thanks to `RelationshipRepository
    .upsert` and the `(wallet_a, wallet_b, relationship_type)` unique
    constraint.
    """
    repo = RelationshipRepository(session)
    written = 0
    for cluster in clusters:
        confidence = Decimal(str(round(cluster_confidence(cluster), 2)))
        wallets = cluster.wallet_addresses
        for i, wallet_a in enumerate(wallets):
            for wallet_b in wallets[i + 1 :]:
                a, b = sorted((wallet_a, wallet_b))
                await repo.upsert(
                    WalletRelationshipDTO(
                        wallet_a=a,
                        wallet_b=b,
                        relationship_type=RELATIONSHIP_TYPE,
                        confidence=confidence,
                    )
                )
                written += 1
    return written
