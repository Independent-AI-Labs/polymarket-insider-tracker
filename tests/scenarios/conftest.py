"""Shared fixtures for the end-to-end scenario suite.

Every scenario runs through the production code paths
(detector stack, rollup aggregator, newsletter builder,
himalaya batch send) — the only things mocked are
(a) the Polygon / Gamma external lookups via the `backtest.replay`
resolver protocols and (b) the clock via `freeze_time`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from polymarket_insider_tracker.storage.models import Base


REPO_ROOT = Path(__file__).resolve().parents[2]
HIMALAYA_BIN = REPO_ROOT / ".boot-linux" / "bin" / "himalaya"
# The actual binary lives in the AMI-AGENTS root two levels up.
# Resolve it by walking up until we find .boot-linux/bin/himalaya.
_candidate = Path(__file__).resolve()
while _candidate != _candidate.parent:
    cand = _candidate / ".boot-linux" / "bin" / "himalaya"
    if cand.exists():
        HIMALAYA_BIN = cand
        break
    _candidate = _candidate.parent


def pytest_addoption(parser: pytest.Parser) -> None:
    """Harness-wide CLI flags.

    --update-snapshots: rewrite any golden-HTML file whose diff fails
    the scenario assertion. Mirrors the behaviour of
    `pytest --snapshot-update` but hand-rolled so we don't pull in
    another plugin.
    """
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        default=False,
        help="Overwrite golden-HTML files instead of asserting",
    )


@pytest.fixture
def update_snapshots(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-snapshots"))


@pytest.fixture
async def scenario_db_url(tmp_path: Path) -> AsyncGenerator[str, None]:
    """Per-test SQLite file so scenarios are hermetic.

    Pytest's `:memory:` doesn't share across async connections, so
    we use a tmp_path file instead. Auto-cleaned with the tmp_path
    fixture.
    """
    db_path = tmp_path / "scenario.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield url


@pytest.fixture
async def scenario_session_factory(scenario_db_url: str):
    """Session-factory for tests that need direct DB access alongside
    the Scenario harness (seeding funding_transfers rows etc.)."""
    engine = create_async_engine(scenario_db_url, echo=False)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture(scope="session")
def himalaya_binary() -> str:
    """Resolve the himalaya binary path or skip the session-scoped test.

    Also asserts the required feature flags (`+batch`, `+template-vars`)
    are compiled in — a binary missing `+batch` would make every
    scenario pass-skip in a silently useless way.
    """
    import subprocess

    if not HIMALAYA_BIN.exists():
        pytest.skip(f"himalaya binary not found at {HIMALAYA_BIN}")
    result = subprocess.run(
        [str(HIMALAYA_BIN), "--version"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"{HIMALAYA_BIN} --version failed: {result.stderr}")
    for flag in ("+batch", "+template-vars"):
        if flag not in result.stdout:
            pytest.skip(
                f"himalaya binary missing required feature {flag!r} "
                f"(got: {result.stdout.strip()})"
            )
    return str(HIMALAYA_BIN)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin `datetime.now(tz=...)` to a stable UTC timestamp.

    Returns the ISO string the tests can reference in assertions.
    Only used by scenarios that compare against golden files.
    """
    from datetime import UTC, datetime

    frozen_iso = "2026-04-19T13:00:00+00:00"
    frozen = datetime.fromisoformat(frozen_iso)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen.replace(tzinfo=None)
            return frozen.astimezone(tz)

    # Patch each module that calls datetime.now(UTC) in the
    # hot path. Keeping this list explicit means new call sites
    # surface as test failures rather than silently drift.
    for module in (
        "polymarket_insider_tracker.alerter.history",
        "polymarket_insider_tracker.storage.repos",
        "polymarket_insider_tracker.backtest.replay",
    ):
        try:
            monkeypatch.setattr(f"{module}.datetime", _FrozenDateTime)
        except (AttributeError, ModuleNotFoundError):
            # Some modules import datetime but not under that symbol —
            # the scenarios that use frozen_clock should re-enforce it
            # locally if a specific site matters.
            pass

    return frozen_iso
