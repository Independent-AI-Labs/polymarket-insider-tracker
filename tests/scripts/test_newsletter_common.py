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
