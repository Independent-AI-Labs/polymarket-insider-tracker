"""Unit tests for the bounce-drain parser + orchestration."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bounce-drain.py"


@pytest.fixture(scope="module")
def drain_mod():
    import sys
    spec = importlib.util.spec_from_file_location("_bounce_drain", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses needs the module registered in sys.modules at class-
    # creation time so it can look up the containing namespace.
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return mod


class TestParseBounceLine:
    def test_happy_path(self, drain_mod):
        raw = (
            '{"email":"a@x.com","bounce_type":"hard","message_id":"<m1@relay>",'
            '"diagnostic":"550 user unknown","reported_at":"2026-04-19T12:00:00+00:00"}'
        )
        rec = drain_mod.parse_bounce_line(raw)
        assert rec is not None
        assert rec.email == "a@x.com"
        assert rec.bounce_type == "hard"
        assert rec.message_id == "<m1@relay>"
        assert rec.diagnostic == "550 user unknown"
        assert rec.reported_at == datetime(2026, 4, 19, 12, 0, tzinfo=UTC)

    def test_lowercases_email(self, drain_mod):
        rec = drain_mod.parse_bounce_line(
            '{"email":"A@X.COM","bounce_type":"soft"}'
        )
        assert rec.email == "a@x.com"

    def test_unix_timestamp(self, drain_mod):
        rec = drain_mod.parse_bounce_line(
            '{"email":"a@x.com","bounce_type":"hard","reported_at":1713528000}'
        )
        assert rec.reported_at.year == 2024  # matches the unix value

    def test_missing_reported_at_defaults_to_now(self, drain_mod):
        rec = drain_mod.parse_bounce_line(
            '{"email":"a@x.com","bounce_type":"hard"}'
        )
        assert rec.reported_at.tzinfo is not None

    def test_invalid_json_returns_none(self, drain_mod):
        assert drain_mod.parse_bounce_line("not-json") is None

    def test_blank_line_returns_none(self, drain_mod):
        assert drain_mod.parse_bounce_line("   ") is None

    def test_missing_email_rejected(self, drain_mod):
        assert drain_mod.parse_bounce_line('{"bounce_type":"hard"}') is None

    def test_invalid_bounce_type_rejected(self, drain_mod):
        assert drain_mod.parse_bounce_line(
            '{"email":"a@x.com","bounce_type":"catastrophic"}'
        ) is None


class TestIterBounces:
    def test_skips_blanks_and_malformed(self, drain_mod):
        src = StringIO(
            '\n'
            '{"email":"a@x.com","bounce_type":"hard"}\n'
            'not-json\n'
            '{"email":"b@x.com","bounce_type":"soft"}\n'
        )
        records = list(drain_mod.iter_bounces(src))
        assert [r.email for r in records] == ["a@x.com", "b@x.com"]
