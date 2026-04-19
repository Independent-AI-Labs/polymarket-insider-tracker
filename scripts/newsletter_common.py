"""Shared helpers for the polymarket newsletter trilogy.

Each cadence (daily / weekly / monthly) has its own data builder and
Tera template but shares the himalaya delivery plumbing, target-list
filtering, and the tempfile-YAML handoff with `batch send`.

This module is imported by `send-report.py` (daily), plus the
new `weekly-newsletter.py` and `monthly-newsletter.py`.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

import yaml

# Bold markdown `**X**` → HTML span. Pre-rendered in Python so Tera
# templates emit observations with `| safe` and stay straight iterator
# logic.
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


def render_bold(raw: str) -> str:
    """Convert `**X**` spans to `<strong>X</strong>`."""
    return BOLD_RE.sub(r"<strong>\1</strong>", raw)


def filter_targets(targets: list[dict], names: str | None) -> list[dict]:
    """Narrow the recipient list by `--targets a,b,c` or the `enabled` flag.

    Explicit `--targets` selection overrides `enabled=False`, so an
    operator can force-send to a normally-disabled archive address.
    """
    if names:
        requested = {t.strip() for t in names.split(",")}
        return [t for t in targets if t["name"] in requested]
    return [t for t in targets if t.get("enabled", True)]


def deliver_via_himalaya(
    rows: list[dict],
    *,
    template_path: Path,
    subject_template: str,
    account: str,
    rate: str = "5/min",
    attachments: Iterable[Path] = (),
    dry_run: bool = False,
) -> int:
    """Drive `himalaya batch send` against `rows`.

    `rows` is a list of per-recipient dicts; each dict must carry at
    least `email`, `name`, `subject` (the final rendered subject
    string — Tera renders `{{ subject }}` in the template). Extra
    columns are available to the template under whatever key the
    caller chose.
    """
    if not rows:
        print("  [WARN] No delivery targets matched")
        return 0

    cmd: list[str] = []

    # NamedTemporaryFile is the context manager; cleanup is guaranteed
    # on both success and exception.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="polymarket-targets-",
    ) as fh:
        yaml.safe_dump(rows, fh, allow_unicode=True, sort_keys=False)
        fh.flush()

        cmd = [
            "himalaya", "batch", "send",
            "--account", account,
            "--template", str(template_path),
            "--data", fh.name,
            "--subject", subject_template,
            "--rate", rate,
            "--yes",
        ]
        for attachment in attachments:
            cmd.extend(["--attachment", str(attachment)])
        if dry_run:
            cmd.append("--dry-run")

        print(
            f"  → himalaya batch send → {len(rows)} recipient(s) via account "
            f"{account!r}"
        )
        for r in rows:
            print(f"    • {r['email']}  subject={r.get('subject', '(none)')!r}")

        result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  [ERROR] himalaya batch send failed (rc={result.returncode})")
        if result.stderr:
            print(result.stderr.rstrip())
        if result.stdout:
            print(result.stdout.rstrip())
    else:
        out = result.stdout.strip()
        if out:
            for line in out.splitlines():
                print(f"    {line}")

    return result.returncode
