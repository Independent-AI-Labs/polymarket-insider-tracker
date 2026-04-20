"""Metrics persistence — per-edition snapshot of wallet + market aggregates.

Storage layer only. No stat computation, no profile logic. This package
takes what the composer already produced (`DailyReport`, wallets-to-watch,
promoted-markets) and writes a typed, versioned JSON snapshot to disk so
Phase 3 of the profile-system plan has accumulated historical data to
compute pairwise correlations + silhouette scores over.

Layout:
    <root>/
      snapshots/
        YYYY/
          YYYY-MM-DD.json        ← one DailyMetricsSnapshot per edition
      index.jsonl                ← append-only MetricsIndex per write

`<root>` resolves in this order:
    1. `POLYMARKET_METRICS_ROOT` environment variable
    2. `~/.local/share/polymarket-insider-tracker/metrics/`
"""

from .models import (
    DailyMetricsSnapshot,
    MarketMetric,
    MetricsIndex,
    WalletMetric,
    snapshot_from_report,
)
from .store import MetricsStore, default_root

__all__ = [
    "DailyMetricsSnapshot",
    "MarketMetric",
    "MetricsIndex",
    "MetricsStore",
    "WalletMetric",
    "default_root",
    "snapshot_from_report",
]
