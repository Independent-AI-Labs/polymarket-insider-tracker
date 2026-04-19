"""Unit tests for send-report.py data-file builder + target filter.

The script lives at scripts/send-report.py (hyphenated filename, not a
package import) so we load it via importlib.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "send-report.py"
)


@pytest.fixture(scope="module")
def send_report():
    spec = importlib.util.spec_from_file_location("_send_report", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def base_ctx() -> dict:
    return {
        "date": "2026-04-19",
        "generated": "2026-04-19 13:00",
        "title": "Polymarket Snapshot — 2026-04-19",
        "stats": {"Top-N 24h volume total": "$12.3M"},
        "sections": [
            {
                "title": "Top Markets by 24-Hour Volume",
                "markets": [
                    {
                        "Question": f"Market {i}",
                        "24h Vol": f"${i}K",
                        "Liquidity": f"${i * 10}K",
                        "_raw": {"id": i, "big": "x" * 500},
                    }
                    for i in range(1, 11)
                ],
            },
            {
                "title": "Top Markets by Liquidity",
                "markets": [
                    {"Question": "q", "_raw": {"id": 99, "junk": "z" * 200}},
                ],
            },
        ],
        "observations": [
            f"**bold-{i}**: observation number {i}"
            for i in range(10)
        ],
        "config": {},
    }


@pytest.fixture
def cfg() -> dict:
    return {
        "email": {
            "subject_template": "[AMI] Polymarket Snapshot — {date}",
            "summary_top_n": 3,
            "max_observations": 4,
        },
        "delivery": {
            "account": "polymarket",
            "rate": "5/min",
            "targets": [
                {"name": "vlad", "email": "vlad@example.com", "enabled": True},
                {
                    "name": "team",
                    "email": "team@example.com",
                    "enabled": False,
                },
                {
                    "name": "archive",
                    "email": "archive@example.com",
                    "enabled": True,
                    "subject_template": "[Archive] {date}",
                },
            ],
        },
    }


class TestFilterTargets:
    def test_default_filters_disabled(self, send_report, cfg):
        out = send_report.filter_targets(cfg["delivery"]["targets"], None)
        names = [t["name"] for t in out]
        assert names == ["vlad", "archive"]  # "team" is enabled=False

    def test_explicit_filter(self, send_report, cfg):
        out = send_report.filter_targets(
            cfg["delivery"]["targets"], "vlad,archive",
        )
        assert [t["name"] for t in out] == ["vlad", "archive"]

    def test_explicit_filter_picks_disabled(self, send_report, cfg):
        # Explicit --targets overrides the enabled flag. This lets an
        # operator force-send to an archive address that's normally off.
        out = send_report.filter_targets(cfg["delivery"]["targets"], "team")
        assert [t["name"] for t in out] == ["team"]

    def test_whitespace_tolerant(self, send_report, cfg):
        out = send_report.filter_targets(
            cfg["delivery"]["targets"], " vlad , archive ",
        )
        assert [t["name"] for t in out] == ["vlad", "archive"]

    def test_unknown_name_produces_empty(self, send_report, cfg):
        out = send_report.filter_targets(cfg["delivery"]["targets"], "nobody")
        assert out == []


class TestBuildTargetsData:
    def test_row_per_target(self, send_report, cfg, base_ctx):
        targets = cfg["delivery"]["targets"][:2]
        rows = send_report.build_targets_data(cfg, targets, base_ctx)
        assert len(rows) == 2
        for row, target in zip(rows, targets):
            assert row["email"] == target["email"]
            assert row["name"] == target["name"]
            assert "report" in row

    def test_default_subject(self, send_report, cfg, base_ctx):
        rows = send_report.build_targets_data(
            cfg, [cfg["delivery"]["targets"][0]], base_ctx,
        )
        assert rows[0]["subject"] == "[AMI] Polymarket Snapshot — 2026-04-19"

    def test_per_target_subject_override(self, send_report, cfg, base_ctx):
        rows = send_report.build_targets_data(
            cfg, [cfg["delivery"]["targets"][2]], base_ctx,
        )
        assert rows[0]["subject"] == "[Archive] 2026-04-19"

    def test_raw_blob_dropped(self, send_report, cfg, base_ctx):
        rows = send_report.build_targets_data(
            cfg, [cfg["delivery"]["targets"][0]], base_ctx,
        )
        sections = rows[0]["report"]["sections"]
        assert sections, "sections must be non-empty"
        for section in sections:
            for m in section["markets"]:
                assert "_raw" not in m, "raw Polymarket blob leaked into row"

    def test_summary_trimmed(self, send_report, cfg, base_ctx):
        rows = send_report.build_targets_data(
            cfg, [cfg["delivery"]["targets"][0]], base_ctx,
        )
        assert len(rows[0]["report"]["summary_markets"]) == 3  # summary_top_n

    def test_observations_trimmed_and_bolded(self, send_report, cfg, base_ctx):
        rows = send_report.build_targets_data(
            cfg, [cfg["delivery"]["targets"][0]], base_ctx,
        )
        obs = rows[0]["report"]["observations"]
        assert len(obs) == 4  # max_observations
        # `**bold-0**` → `<strong>bold-0</strong>`
        assert obs[0].startswith("<strong>bold-0</strong>")
        for o in obs:
            assert "**" not in o, "raw ** markdown leaked into HTML output"

    def test_extra_sections_only_after_first(self, send_report, cfg, base_ctx):
        rows = send_report.build_targets_data(
            cfg, [cfg["delivery"]["targets"][0]], base_ctx,
        )
        assert len(rows[0]["report"]["extra_sections"]) == 1
        assert rows[0]["report"]["extra_sections"][0]["title"] \
            == "Top Markets by Liquidity"

    def test_row_shape_is_yaml_safe(self, send_report, cfg, base_ctx):
        import yaml
        rows = send_report.build_targets_data(
            cfg, cfg["delivery"]["targets"][:1], base_ctx,
        )
        # Round-trip through yaml — this is what himalaya will read.
        serialized = yaml.safe_dump(rows, allow_unicode=True, sort_keys=False)
        restored = yaml.safe_load(serialized)
        assert restored[0]["email"] == "vlad@example.com"
        assert restored[0]["report"]["summary_markets"]
