#!/usr/bin/env python3
"""Open a golden HTML snapshot in $BROWSER + capture the review.

Task 7.2.1 — operator runs this after `pytest --update-snapshots`
changes a file under `tests/scenarios/fixtures/golden/`. The script
shows the git diff vs the previous version, opens the HTML in a
browser, prompts y/n, and writes the approval to `.reviewed.yaml`
so CI (task 7.2.2) can verify every golden was reviewed.

Usage:
    uv run python scripts/review-snapshot.py \\
        tests/scenarios/fixtures/golden/fresh-wallet-daily.html
    uv run python scripts/review-snapshot.py --all
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = REPO_ROOT / "tests" / "scenarios" / "fixtures" / "golden"
REVIEW_FILE = GOLDEN_DIR / ".reviewed.yaml"


def _git_diff(path: Path) -> str:
    """Show diff vs the committed version, empty string if unchanged."""
    try:
        result = subprocess.run(
            ["git", "diff", "--", str(path)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout
    except FileNotFoundError:
        return ""


def _load_reviews() -> dict[str, dict]:
    if not REVIEW_FILE.exists():
        return {}
    data = yaml.safe_load(REVIEW_FILE.read_text()) or {}
    return data.get("reviews", {})


def _save_reviews(reviews: dict[str, dict]) -> None:
    REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_FILE.write_text(
        yaml.safe_dump({"reviews": reviews}, sort_keys=True)
    )


def review(path: Path, *, reviewer: str, interactive: bool = True) -> bool:
    """Record a review of `path` by `reviewer`. Returns True if approved."""
    if not path.exists():
        print(f"[ERROR] {path} does not exist", file=sys.stderr)
        return False

    relative = str(path.relative_to(REPO_ROOT))
    print(f"\n=== {relative} ===\n")
    diff = _git_diff(path)
    if diff:
        print("git diff vs HEAD:")
        for line in diff.splitlines()[:40]:
            print(f"  {line}")
    else:
        print("(no uncommitted diff)")

    if interactive:
        # Open in browser so the operator eyeballs the rendered output.
        try:
            webbrowser.open(path.as_uri())
        except Exception as exc:  # noqa: BLE001 — CI runs non-interactive
            print(f"[warn] could not open browser: {exc}")
        answer = input("Approve this golden? [y/N] ").strip().lower()
        approved = answer == "y"
    else:
        approved = True

    reviews = _load_reviews()
    reviews[relative] = {
        "reviewer": reviewer,
        "approved": approved,
        "reviewed_at": datetime.now(UTC).isoformat(),
    }
    _save_reviews(reviews)
    return approved


def check_all_reviewed() -> int:
    """Task 7.2.2 — CI gate.

    For every `*.html` under the golden dir, confirm a review entry
    exists in `.reviewed.yaml` newer than the file's last-modified
    mtime. Exits non-zero if any golden is unreviewed or stale.
    """
    reviews = _load_reviews()
    problems: list[str] = []
    for html in GOLDEN_DIR.glob("*.html"):
        relative = str(html.relative_to(REPO_ROOT))
        if relative not in reviews:
            problems.append(f"{relative}: no review entry")
            continue
        entry = reviews[relative]
        if not entry.get("approved", False):
            problems.append(f"{relative}: not approved")
            continue
        try:
            reviewed_at = datetime.fromisoformat(entry["reviewed_at"])
        except (KeyError, ValueError):
            problems.append(f"{relative}: malformed reviewed_at")
            continue
        mtime = datetime.fromtimestamp(html.stat().st_mtime, tz=UTC)
        if mtime > reviewed_at:
            problems.append(
                f"{relative}: file modified after last review "
                f"(mtime={mtime.isoformat()}, reviewed={entry['reviewed_at']})"
            )
    if problems:
        print("Golden review gate FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"Golden review gate OK ({len(list(GOLDEN_DIR.glob('*.html')))} files)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review golden-HTML snapshots")
    parser.add_argument("path", nargs="?", type=Path, help="Golden file to review")
    parser.add_argument("--all", action="store_true", help="Review every golden")
    parser.add_argument(
        "--check",
        action="store_true",
        help="CI mode — just verify every golden has a fresh review",
    )
    parser.add_argument(
        "--reviewer",
        default=os.environ.get("USER", "unknown"),
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Skip browser + prompt (for scripted approvals).",
    )
    args = parser.parse_args(argv)

    if args.check:
        return check_all_reviewed()

    targets: list[Path] = []
    if args.all:
        targets = sorted(GOLDEN_DIR.glob("*.html"))
    elif args.path is not None:
        targets = [args.path.resolve()]
    else:
        parser.error("provide a path or --all or --check")

    overall_ok = True
    for path in targets:
        approved = review(
            path,
            reviewer=args.reviewer,
            interactive=not args.non_interactive,
        )
        overall_ok = overall_ok and approved
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
