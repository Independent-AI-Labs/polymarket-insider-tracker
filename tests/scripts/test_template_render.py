"""End-to-end render check: `himalaya batch send --dry-run` against the
Tera template + a fixture data file. Skipped when the himalaya binary is
not on PATH (e.g. on machines where ami-mail is not installed).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "scripts" / "templates"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"

# Cadence -> (template filename, fixture filename).
CADENCE_TEMPLATES = {
    "daily":   ("polymarket-newsletter.html", "targets-sample.yaml"),
    "weekly":  ("polymarket-weekly.html",     "weekly-targets-sample.yaml"),
    "monthly": ("polymarket-monthly.html",    "monthly-targets-sample.yaml"),
}


@pytest.fixture(scope="module")
def himalaya() -> str:
    binary = shutil.which("himalaya")
    if not binary:
        pytest.skip("himalaya binary not on PATH")
    return binary


@pytest.fixture(scope="module")
def himalaya_config(tmp_path_factory) -> Path:
    """Inline himalaya config so dry-run never tries to reach a real relay."""
    config_path = tmp_path_factory.mktemp("himalaya-cfg") / "himalaya.toml"
    config_path.write_text(
        """
[accounts.polymarket]
email = "ami-reports@ami.local"
backend.type = "none"

message.send.backend.type            = "smtp"
message.send.backend.host            = "127.0.0.1"
message.send.backend.port            = 2525
message.send.backend.encryption.type = "none"
message.send.backend.login           = "ami-reports@ami.local"
message.send.backend.auth.type       = "password"
message.send.backend.auth.raw        = "unused-in-dry-run"

message.save-copy = false
"""
    )
    return config_path


@pytest.mark.parametrize("cadence", sorted(CADENCE_TEMPLATES.keys()))
def test_batch_send_dry_run_renders(
    himalaya: str, himalaya_config: Path, cadence: str
) -> None:
    template_name, fixture_name = CADENCE_TEMPLATES[cadence]
    template_path = TEMPLATES_DIR / template_name
    fixture_path = FIXTURES_DIR / fixture_name

    result = subprocess.run(
        [
            himalaya,
            "--output", "json",
            "batch", "send",
            "--config", str(himalaya_config),
            "--account", "polymarket",
            "--template", str(template_path),
            "--data", str(fixture_path),
            "--subject", "{{ subject }}",
            "--dry-run",
            "--yes",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"himalaya [{cadence}] exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    import json
    summary = json.loads(result.stdout)
    assert summary["total"] == 1, f"[{cadence}] expected 1 row, got {summary}"
    emails = [r["email"] for r in summary["results"]]
    assert "alice@example.com" in emails
    assert summary["results"][0]["status"] == "dry-run"
