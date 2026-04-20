"""JSON-on-disk persistence for `DailyMetricsSnapshot`.

One file per edition under `snapshots/YYYY/YYYY-MM-DD.json`; every
write also appends a `MetricsIndex` line to `index.jsonl` for fast
range scans. Writes are atomic (write-to-tempfile-then-`os.replace`
in the same directory) so a SIGTERM mid-write never leaves a partial
file for the next reader to trip on.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from datetime import date as date_cls
from pathlib import Path

from .models import DailyMetricsSnapshot, MetricsIndex

LOG = logging.getLogger(__name__)

SNAPSHOT_SUBDIR = "snapshots"
INDEX_FILE = "index.jsonl"

# POLYMARKET_METRICS_ROOT overrides everything else — tests use this.
ENV_ROOT = "POLYMARKET_METRICS_ROOT"


def default_root() -> Path:
    """Resolve the default metrics root.

    1. `POLYMARKET_METRICS_ROOT` env var (tests, ops overrides).
    2. `~/.local/share/polymarket-insider-tracker/metrics/`.
    """
    override = os.environ.get(ENV_ROOT)
    if override:
        return Path(override).expanduser()
    return (
        Path.home()
        / ".local"
        / "share"
        / "polymarket-insider-tracker"
        / "metrics"
    )


def _snapshot_rel_path(d: date_cls) -> str:
    return f"{SNAPSHOT_SUBDIR}/{d.year:04d}/{d.isoformat()}.json"


def _atomic_write_text(path: Path, data: str) -> None:
    """Write text to `path` atomically.

    Uses a NamedTemporaryFile in the same directory then `os.replace`.
    Same-directory rename is POSIX-atomic on the same filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # delete=False so we can close before replace; we clean up on error.
    # SIM115 doesn't fit — we intentionally manage lifetime manually
    # so the temp path survives past close() for os.replace().
    fd = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(fd.name)
    try:
        try:
            fd.write(data)
            fd.flush()
            os.fsync(fd.fileno())
        finally:
            fd.close()
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup; re-raise so caller sees the failure.
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        raise


class MetricsStore:
    """Disk-backed store for `DailyMetricsSnapshot` payloads."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root: Path = Path(root).expanduser() if root else default_root()

    # ── Writing ────────────────────────────────────────────────────

    def write_snapshot(self, snapshot: DailyMetricsSnapshot) -> Path:
        """Persist the snapshot and append an index entry.

        Returns the absolute path to the snapshot file. Overwrites if
        an edition for the same date already exists (newsletter reruns
        produce fresh data — no reason to keep stale).
        """
        d = date_cls.fromisoformat(snapshot.date)
        rel = _snapshot_rel_path(d)
        abs_path = self.root / rel

        # Pydantic v2: model_dump_json handles datetime / decimal
        # correctly; mode="json" on model_dump would let us customise
        # but stock JSON output is fine here.
        payload = snapshot.model_dump_json(indent=2)
        _atomic_write_text(abs_path, payload)

        index_entry = MetricsIndex(
            date=snapshot.date,
            edition_id=snapshot.edition_id,
            wallet_count=len(snapshot.wallets),
            market_count=len(snapshot.markets),
            total_notional=snapshot.total_notional,
            snapshot_file=rel,
            written_at=datetime.now(tz=UTC),
        )
        self._append_index(index_entry)
        return abs_path

    def _append_index(self, entry: MetricsIndex) -> None:
        """Append one JSON line to `<root>/index.jsonl`."""
        idx_path = self.root / INDEX_FILE
        idx_path.parent.mkdir(parents=True, exist_ok=True)
        line = entry.model_dump_json() + "\n"
        # Append mode — small line, single write is effectively atomic
        # on POSIX for lines < PIPE_BUF when opened with O_APPEND. For
        # safety against truncation under concurrent writers we keep
        # this simple: this process is the only writer per edition.
        with idx_path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    # ── Reading ────────────────────────────────────────────────────

    def load_snapshot(self, d: date_cls) -> DailyMetricsSnapshot | None:
        """Load the snapshot for `d`, or return None if missing.

        Missing is not an error — Phase 3 will skim arbitrary date
        ranges and must tolerate gaps.
        """
        abs_path = self.root / _snapshot_rel_path(d)
        if not abs_path.exists():
            return None
        try:
            text = abs_path.read_text(encoding="utf-8")
        except OSError:
            LOG.exception("failed reading snapshot %s", abs_path)
            return None
        try:
            return DailyMetricsSnapshot.model_validate_json(text)
        except ValueError:
            LOG.exception("corrupt snapshot at %s", abs_path)
            return None

    def list_range(
        self, start: date_cls, end: date_cls
    ) -> list[MetricsIndex]:
        """Return all index entries with `start <= date <= end`.

        Reads `index.jsonl` once. If a date appears more than once
        (e.g. rerun) the latest entry wins — duplicates are
        de-duplicated by `date` keeping the most recently-written one.
        """
        idx_path = self.root / INDEX_FILE
        if not idx_path.exists():
            return []
        by_date: dict[str, MetricsIndex] = {}
        with idx_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = MetricsIndex.model_validate_json(line)
                except ValueError:
                    LOG.warning("skipping malformed index line: %s", line[:120])
                    continue
                d = entry.date_obj()
                if start <= d <= end:
                    by_date[entry.date] = entry
        return sorted(by_date.values(), key=lambda e: e.date)

    def iter_snapshots(
        self, start: date_cls, end: date_cls
    ) -> Iterator[DailyMetricsSnapshot]:
        """Yield snapshots in date order for the given inclusive range.

        Skips missing / corrupt editions silently; Phase 3 diagnostics
        compute over whatever editions exist without caring about gaps.
        """
        for entry in self.list_range(start, end):
            snap = self.load_snapshot(entry.date_obj())
            if snap is not None:
                yield snap

    # ── Retention ──────────────────────────────────────────────────

    def retention_prune(self, keep_days: int) -> int:
        """Delete snapshots older than `keep_days` days (from today UTC).

        Rewrites `index.jsonl` in-place (atomic-write style) to drop
        pruned entries. Returns the count of snapshot files deleted
        (not counting the index rewrite).
        """
        if keep_days < 0:
            raise ValueError("keep_days must be >= 0")

        today = datetime.now(tz=UTC).date()
        cutoff = date_cls.fromordinal(today.toordinal() - keep_days)

        snap_dir = self.root / SNAPSHOT_SUBDIR
        deleted = 0
        if snap_dir.exists():
            for year_dir in snap_dir.iterdir():
                if not year_dir.is_dir():
                    continue
                for snap_file in year_dir.iterdir():
                    if not snap_file.is_file() or snap_file.suffix != ".json":
                        continue
                    stem = snap_file.stem  # "YYYY-MM-DD"
                    try:
                        d = date_cls.fromisoformat(stem)
                    except ValueError:
                        continue
                    if d < cutoff:
                        try:
                            snap_file.unlink()
                            deleted += 1
                        except OSError:
                            LOG.exception(
                                "failed to delete %s", snap_file
                            )
                # Best-effort empty-directory cleanup. Safe because a
                # later write recreates it.
                try:
                    next(year_dir.iterdir())
                except StopIteration:
                    with contextlib.suppress(OSError):
                        year_dir.rmdir()

        # Rewrite index.jsonl dropping pruned dates.
        idx_path = self.root / INDEX_FILE
        if idx_path.exists():
            kept_lines: list[str] = []
            with idx_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        entry = MetricsIndex.model_validate_json(raw)
                    except ValueError:
                        # Preserve malformed lines verbatim — pruning
                        # isn't an index-repair tool.
                        kept_lines.append(raw)
                        continue
                    if entry.date_obj() >= cutoff:
                        kept_lines.append(raw)
            new_body = "\n".join(kept_lines)
            if new_body:
                new_body += "\n"
            _atomic_write_text(idx_path, new_body)

        return deleted
