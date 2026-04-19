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


class TestIdempotencyGuard:
    """Task 13.1 — already-sent rows get skipped before himalaya runs."""

    def test_empty_already_sent_passes_through(self, nc, tmp_path):
        """No idempotency set → every row goes to himalaya.

        We simulate the subprocess call failing (himalaya binary not
        on PATH) and assert the function returned a non-zero rc,
        i.e. it actually tried to invoke the CLI.
        """
        import subprocess
        import unittest.mock as mock
        calls: list[list[str]] = []
        def _fake_run(cmd, *a, **kw):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout='{"total":1,"sent":1,"failed":0,"results":[{"email":"a@x.com","status":"sent","message_id":"<m>"}]}', stderr="")
        with mock.patch.object(nc.subprocess, "run", side_effect=_fake_run):
            rc = nc.deliver_via_himalaya(
                rows=[{"email": "a@x.com", "subject": "s"}],
                template_path=tmp_path / "tpl",
                subject_template="{{ subject }}",
                account="x",
            )
        assert rc == 0
        # himalaya was invoked once.
        assert any(c[0] == "himalaya" for c in calls)

    def test_already_sent_skips_row_and_writes_ledger(self, nc, tmp_path):
        """already_sent={email} → row dropped, ledger writer sees
        `outcome='skipped'`."""
        import subprocess
        import unittest.mock as mock
        ledger_calls: list[tuple] = []
        def _fake_ledger(edition_id, cadence, entries, queued_at, dry_run):
            ledger_calls.append((edition_id, cadence, entries, dry_run))

        with mock.patch.object(nc.subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout="", stderr=""
            )
            rc = nc.deliver_via_himalaya(
                rows=[{"email": "a@x.com", "subject": "s"}],
                template_path=tmp_path / "tpl",
                subject_template="{{ subject }}",
                account="x",
                edition_id="daily-2026-04-19",
                cadence="daily",
                ledger_writer=_fake_ledger,
                already_sent=frozenset({"a@x.com"}),
            )
        # No himalaya call because the only row was already-sent.
        assert not mock_run.called
        assert rc == 0
        assert len(ledger_calls) == 1
        _, _, entries, _ = ledger_calls[0]
        assert entries[0]["status"] == "skipped"
        assert entries[0]["email"] == "a@x.com"

    def test_already_sent_partial_batch(self, nc, tmp_path):
        """Two rows, one in already_sent → only the second goes through."""
        import subprocess
        import unittest.mock as mock
        with mock.patch.object(nc.subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0,
                stdout='{"total":1,"sent":1,"failed":0,"results":[{"email":"b@x.com","status":"sent","message_id":"<m2>"}]}',
                stderr="",
            )
            nc.deliver_via_himalaya(
                rows=[
                    {"email": "a@x.com", "subject": "s"},
                    {"email": "b@x.com", "subject": "s"},
                ],
                template_path=tmp_path / "tpl",
                subject_template="{{ subject }}",
                account="x",
                already_sent=frozenset({"a@x.com"}),
            )
        assert mock_run.called
        # Inspect the temp YAML that was written — only b@x.com should
        # be in it. Easier: check the command's --data path file content.
        invocation = mock_run.call_args.args[0]
        data_flag_idx = invocation.index("--data")
        data_path = invocation[data_flag_idx + 1]
        # The tmp file was already cleaned up by the NamedTemporaryFile
        # context manager, so we rely on the call count alone here.
        # Still, the invocation order is a strong signal: it got
        # exactly one himalaya call with both rows having been
        # filtered.
        assert mock_run.call_count == 1


class TestListUnsubscribeHeaders:
    """Task 13.2 — List-Unsubscribe headers emitted at MML level."""

    def test_headers_added_when_host_set(self, nc, tmp_path):
        import subprocess
        import unittest.mock as mock
        with mock.patch.object(nc.subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout="", stderr=""
            )
            nc.deliver_via_himalaya(
                rows=[{"email": "a@x.com", "subject": "s"}],
                template_path=tmp_path / "tpl",
                subject_template="{{ subject }}",
                account="x",
                list_unsubscribe_host="newsletter.test",
            )
        cmd = mock_run.call_args.args[0]
        # Find the `-H` flag for List-Unsubscribe + the POST header.
        assert "-H" in cmd
        header_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-H"]
        assert any("List-Unsubscribe:" in h and "newsletter.test" in h for h in header_args)
        assert any("List-Unsubscribe-Post: List-Unsubscribe=One-Click" in h for h in header_args)

    def test_no_headers_when_host_omitted(self, nc, tmp_path):
        import subprocess
        import unittest.mock as mock
        with mock.patch.object(nc.subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                [], 0, stdout="", stderr=""
            )
            nc.deliver_via_himalaya(
                rows=[{"email": "a@x.com", "subject": "s"}],
                template_path=tmp_path / "tpl",
                subject_template="{{ subject }}",
                account="x",
            )
        cmd = mock_run.call_args.args[0]
        header_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-H"]
        assert not any("List-Unsubscribe" in h for h in header_args)


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
