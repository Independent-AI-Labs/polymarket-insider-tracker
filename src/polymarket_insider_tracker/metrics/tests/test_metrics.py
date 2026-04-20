"""Unit tests for the metrics persistence layer.

Storage-only surface: round-trip, index append, range query, retention
prune, missing-date tolerance. No composer / no stat logic here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timezone
from pathlib import Path

import pytest

from polymarket_insider_tracker.metrics import (
    DailyMetricsSnapshot,
    MarketMetric,
    MetricsStore,
    WalletMetric,
)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MetricsStore:
    """MetricsStore rooted under `tmp_path`. Also sanity-checks that
    the `POLYMARKET_METRICS_ROOT` env override routes correctly.
    """
    monkeypatch.setenv("POLYMARKET_METRICS_ROOT", str(tmp_path))
    return MetricsStore()


def _make_snapshot(d: date, *, notional: float = 100_000.0) -> DailyMetricsSnapshot:
    return DailyMetricsSnapshot(
        window_start=datetime(d.year, d.month, d.day, 0, 0, tzinfo=UTC),
        window_end=datetime(d.year, d.month, d.day, 23, 59, tzinfo=UTC),
        edition_id=f"daily-{d.isoformat()}",
        date=d.isoformat(),
        source_label="unit-test",
        wallets={
            "0xabc": WalletMetric(
                address="0xabc",
                address_display="0xabc…abc",
                notional_gross=50_000.0,
                trade_count=3,
                markets_touched=2,
                signals_fired=["01-A", "03-C"],
                categories_touched=["informed_flow", "volume_liquidity"],
                promoted_markets_touched=1,
                is_fresh=True,
                market_ids=["0xmarket-1", "0xmarket-2"],
            ),
        },
        markets={
            "0xmarket-1": MarketMetric(
                condition_id="0xmarket-1",
                title="Example market",
                volume_window=250_000.0,
                last_trade_price=0.42,
                signal_hits=["01-A"],
                categories=["informed_flow"],
                unique_wallets=4,
                trade_count=7,
                promoted=True,
            ),
        },
        signal_counts={"01-A": 2, "03-C": 1},
        category_counts={"informed_flow": 2, "volume_liquidity": 1},
        total_trades=12,
        total_notional=notional,
        unique_wallets_in_window=5,
    )


def test_default_root_respects_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLYMARKET_METRICS_ROOT", str(tmp_path / "custom"))
    from polymarket_insider_tracker.metrics import default_root

    assert default_root() == tmp_path / "custom"


def test_roundtrip_preserves_all_fields(store: MetricsStore) -> None:
    snap = _make_snapshot(date(2026, 4, 20))
    path = store.write_snapshot(snap)
    assert path.exists()

    loaded = store.load_snapshot(date(2026, 4, 20))
    assert loaded is not None
    # Deep equality at the model level — pydantic's __eq__ compares
    # field-by-field, which covers nested wallets / markets dicts.
    assert loaded == snap


def test_load_snapshot_missing_returns_none(store: MetricsStore) -> None:
    assert store.load_snapshot(date(1999, 1, 1)) is None


def test_index_appends_on_each_write(store: MetricsStore) -> None:
    store.write_snapshot(_make_snapshot(date(2026, 4, 18)))
    store.write_snapshot(_make_snapshot(date(2026, 4, 19)))

    index_file = store.root / "index.jsonl"
    assert index_file.exists()
    lines = [
        ln for ln in index_file.read_text(encoding="utf-8").splitlines() if ln
    ]
    assert len(lines) == 2

    # Range query picks both up.
    hits = store.list_range(date(2026, 4, 18), date(2026, 4, 19))
    assert [e.date for e in hits] == ["2026-04-18", "2026-04-19"]
    assert hits[0].wallet_count == 1
    assert hits[0].market_count == 1


def test_iter_snapshots_yields_loaded_models(store: MetricsStore) -> None:
    for d in (date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 5)):
        store.write_snapshot(_make_snapshot(d))

    snaps = list(store.iter_snapshots(date(2026, 4, 1), date(2026, 4, 5)))
    assert [s.date for s in snaps] == ["2026-04-01", "2026-04-02", "2026-04-05"]
    assert all(isinstance(s, DailyMetricsSnapshot) for s in snaps)


def test_retention_prune_removes_old_snapshots(
    store: MetricsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Freeze "today" so the test is deterministic across midnight.
    fixed_today = date(2026, 4, 20)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return datetime(
                fixed_today.year,
                fixed_today.month,
                fixed_today.day,
                12,
                0,
                tzinfo=tz or UTC,
            )

    monkeypatch.setattr(
        "polymarket_insider_tracker.metrics.store.datetime", _FrozenDateTime
    )

    # Three snapshots spanning 5 days: 2026-04-14, 04-17, 04-20.
    for d in (date(2026, 4, 14), date(2026, 4, 17), date(2026, 4, 20)):
        store.write_snapshot(_make_snapshot(d))

    # keep_days=2 → cutoff = 2026-04-18. Files with d < 2026-04-18 prune.
    # That drops 04-14 and 04-17 (both deleted), keeps 04-20.
    deleted = store.retention_prune(keep_days=2)
    assert deleted == 2

    assert store.load_snapshot(date(2026, 4, 14)) is None
    assert store.load_snapshot(date(2026, 4, 17)) is None
    assert store.load_snapshot(date(2026, 4, 20)) is not None

    # Index rewritten to match.
    remaining = store.list_range(date(2026, 1, 1), date(2026, 12, 31))
    assert [e.date for e in remaining] == ["2026-04-20"]


def test_retention_prune_zero_keep_days_removes_only_past(
    store: MetricsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_today = date(2026, 4, 20)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return datetime(
                fixed_today.year,
                fixed_today.month,
                fixed_today.day,
                tzinfo=tz or UTC,
            )

    monkeypatch.setattr(
        "polymarket_insider_tracker.metrics.store.datetime", _FrozenDateTime
    )

    store.write_snapshot(_make_snapshot(date(2026, 4, 19)))
    store.write_snapshot(_make_snapshot(date(2026, 4, 20)))

    # keep_days=0 → cutoff = today; strictly-older (04-19) is pruned.
    deleted = store.retention_prune(keep_days=0)
    assert deleted == 1
    assert store.load_snapshot(date(2026, 4, 20)) is not None


def test_rewrite_same_date_replaces_snapshot(store: MetricsStore) -> None:
    d = date(2026, 4, 20)
    snap_a = _make_snapshot(d, notional=10.0)
    snap_b = _make_snapshot(d, notional=99.0)
    store.write_snapshot(snap_a)
    store.write_snapshot(snap_b)

    loaded = store.load_snapshot(d)
    assert loaded is not None
    assert loaded.total_notional == 99.0

    # Two index lines, but `list_range` dedupes by date to the latest.
    entries = store.list_range(d, d)
    assert len(entries) == 1
    assert entries[0].total_notional == 99.0


def test_atomic_write_leaves_no_tempfiles(store: MetricsStore) -> None:
    store.write_snapshot(_make_snapshot(date(2026, 4, 20)))
    snap_dir = store.root / "snapshots" / "2026"
    leftovers = [p for p in snap_dir.iterdir() if p.name.startswith(".")]
    assert leftovers == []
