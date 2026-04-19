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
TEMPLATE_PATH = (
    PROJECT_ROOT / "scripts" / "templates" / "polymarket-newsletter.html"
)
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "targets-sample.yaml"


@pytest.fixture(scope="module")
def himalaya() -> str:
    binary = shutil.which("himalaya")
    if not binary:
        pytest.skip("himalaya binary not on PATH")
    return binary


def test_batch_send_dry_run_renders(himalaya: str, tmp_path: Path) -> None:
    # himalaya needs *some* account in config to start the command pipeline,
    # but in dry-run + individual mode it never actually connects. Point it
    # at an inline config with a `polymarket` stub so the test is hermetic.
    config_path = tmp_path / "himalaya.toml"
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

    result = subprocess.run(
        [
            himalaya,
            "--output", "json",
            "batch", "send",
            "--config", str(config_path),
            "--account", "polymarket",
            "--template", str(TEMPLATE_PATH),
            "--data", str(FIXTURE_PATH),
            "--subject", "{{ subject }}",
            "--dry-run",
            "--yes",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"himalaya exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # JSON summary surfaces every row's email + status.
    import json
    summary = json.loads(result.stdout)
    assert summary["total"] == 1
    emails = [r["email"] for r in summary["results"]]
    assert "alice@example.com" in emails
    assert summary["results"][0]["status"] == "dry-run"
