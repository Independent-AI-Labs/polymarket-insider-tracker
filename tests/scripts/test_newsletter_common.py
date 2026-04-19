"""Unit tests for scripts/newsletter_common.py.

Covers the pure helpers — himalaya JSON parsing, bold rendering,
target filtering — without actually invoking the himalaya binary.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "newsletter_common.py"


@pytest.fixture(scope="module")
def nc():
    spec = importlib.util.spec_from_file_location("_nc", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRenderBold:
    def test_converts_paired_stars(self, nc):
        assert nc.render_bold("hello **world**") == "hello <strong>world</strong>"

    def test_multiple_pairs(self, nc):
        assert nc.render_bold("**a** and **b**") == "<strong>a</strong> and <strong>b</strong>"

    def test_unpaired_star_left_alone(self, nc):
        assert nc.render_bold("just * one star") == "just * one star"


class TestParseHimalayaSummary:
    def test_happy_path(self, nc):
        raw = (
            '{"total":2,"sent":1,"failed":0,"results":['
            '{"email":"a@x.com","status":"dry-run","message_id":null},'
            '{"email":"b@x.com","status":"sent","message_id":"<xyz@relay>"}'
            ']}'
        )
        result = nc._parse_himalaya_summary(raw)
        assert isinstance(result, list) and len(result) == 2
        assert result[0]["email"] == "a@x.com"
        assert result[1]["message_id"] == "<xyz@relay>"

    def test_invalid_json_returns_none(self, nc):
        assert nc._parse_himalaya_summary("not json") is None

    def test_missing_results_returns_none(self, nc):
        assert nc._parse_himalaya_summary('{"total":0}') is None

    def test_non_list_results_returns_none(self, nc):
        assert nc._parse_himalaya_summary('{"results":"oops"}') is None


class TestFetchDbTargets:
    """Exercises fetch_db_targets against an in-memory SQLite database."""

    def test_returns_active_rows_with_unsubscribe_url(self, nc, tmp_path):
        import asyncio
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from polymarket_insider_tracker.storage.models import Base
        from polymarket_insider_tracker.storage.repos import (
            SubscribersRepository,
            SuppressionEntryDTO,
            SuppressionListRepository,
        )

        db_path = tmp_path / "test.db"
        url = f"sqlite+aiosqlite:///{db_path}"

        async def _bootstrap() -> None:
            engine = create_async_engine(url)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as s:
                subs = SubscribersRepository(s)
                active = await subs.insert_pending(
                    email="active@x.com", cadences=["daily"]
                )
                await subs.confirm_opt_in(active.opt_in_token)
                pending = await subs.insert_pending(
                    email="pending@x.com", cadences=["daily"]
                )
                bad = await subs.insert_pending(
                    email="bad@spam.example", cadences=["daily"]
                )
                await subs.confirm_opt_in(bad.opt_in_token)
                supp = SuppressionListRepository(s)
                await supp.add(
                    SuppressionEntryDTO(
                        pattern="spam.example",
                        pattern_type="domain",
                        reason="abuse",
                    )
                )
                await s.commit()
            await engine.dispose()

        asyncio.run(_bootstrap())

        engine = create_async_engine(url)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            rows = nc.fetch_db_targets(
                factory, "daily", public_host="newsletter.test"
            )
        finally:
            asyncio.run(engine.dispose())

        emails = sorted(r["email"] for r in rows)
        assert emails == ["active@x.com"]
        row = rows[0]
        assert row["unsubscribe_url"].startswith(
            "https://newsletter.test/unsubscribe?token="
        )
        assert "confirmed" in row["reason"].lower()


class TestFilterTargets:
    def test_enabled_only_by_default(self, nc):
        targets = [
            {"name": "a", "email": "a@x.com", "enabled": True},
            {"name": "b", "email": "b@x.com", "enabled": False},
        ]
        assert [t["name"] for t in nc.filter_targets(targets, None)] == ["a"]

    def test_explicit_names_override_enabled(self, nc):
        targets = [
            {"name": "a", "email": "a@x.com", "enabled": True},
            {"name": "b", "email": "b@x.com", "enabled": False},
        ]
        assert [t["name"] for t in nc.filter_targets(targets, "b")] == ["b"]

    def test_whitespace_tolerant(self, nc):
        targets = [
            {"name": "a", "email": "a@x.com", "enabled": True},
            {"name": "b", "email": "b@x.com", "enabled": True},
        ]
        assert [t["name"] for t in nc.filter_targets(targets, " a , b ")] == ["a", "b"]
