#!/usr/bin/env python3
"""Detect and optionally fix upstream DNS hijacks of Polymarket hosts.

Polymarket is DNS-blocked at the ISP / national level in many
jurisdictions — the upstream resolver a consumer line inherits
returns `127.0.0.1` (or similar null route) for `*.polymarket.com`,
while public resolvers (1.1.1.1 / 8.8.8.8) queried directly still
return the correct Cloudflare IPs. This is not a LAN firewall and
not something to fix at the gateway — it's imposed upstream.

This script:

1. Probes each required hostname against (a) the system resolver and
   (b) public resolvers (1.1.1.1, 8.8.8.8) via `dig`.
2. Calls a hostname HIJACKED when the system answer is loopback
   / empty / NXDOMAIN while at least one public resolver returns
   something routable.
3. If `--apply` is passed AND at least one hostname is hijacked,
   appends (or replaces, idempotently) a single marker-delimited
   block in `/etc/hosts` mapping each hijacked hostname to the
   first public-resolver answer. Everything between the markers is
   owned by this script; nothing else in `/etc/hosts` is touched.
4. Without `--apply`, prints a report and exits non-zero if any
   hostname is hijacked — suitable for Makefile `dns-check` targets.

Needs `sudo` to write `/etc/hosts` (obviously). Without sudo, the
script still runs the probe and reports; it just skips the write.

Usage:
    python3 scripts/dns-probe-patch.py                 # dry-run report
    python3 scripts/dns-probe-patch.py --apply         # patch if needed
    sudo python3 scripts/dns-probe-patch.py --apply    # same, with root
    python3 scripts/dns-probe-patch.py --revert        # remove the block
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HOSTS_FILE = Path("/etc/hosts")
MARKER_BEGIN = "# >>> polymarket-insider-tracker dns-fix >>>"
MARKER_END = "# <<< polymarket-insider-tracker dns-fix <<<"

# Hostnames the project actually talks to. Keep this list narrow —
# every entry ends up pinned to a Cloudflare IP via /etc/hosts, which
# is a blunt instrument.
REQUIRED_HOSTS: tuple[str, ...] = (
    "clob.polymarket.com",
    "ws-subscriptions-clob.polymarket.com",
    "gamma-api.polymarket.com",
    "ws-live-data.polymarket.com",
    "data-api.polymarket.com",
    # Front-end hostnames — newsletter links point at these. If the
    # upstream resolver returns a block-page IP, the links M. clicks
    # from Gmail hit a gambling-regulator landing page instead of
    # Polymarket. Probe also catches the "routable but wrong" case,
    # not just loopback.
    "polymarket.com",
    "www.polymarket.com",
)

# Public resolvers used as ground truth. First one that answers wins.
PUBLIC_RESOLVERS: tuple[str, ...] = ("1.1.1.1", "8.8.8.8")

# Anything in this set means "the system resolver is lying about this
# hostname." Loopback + the IPv4 null route + empty answers.
HIJACK_MARKERS: frozenset[str] = frozenset({"", "0.0.0.0", "127.0.0.1", "::1"})

# Suspicious answer fingerprints: ISP / regulator block pages that
# return a routable-but-wrong IP rather than loopback. We detect
# these by comparing against public-resolver answers — any time the
# system answer is NOT in the set of public-resolver answers for the
# same hostname, we flag it.
#
# The check runs per-hostname in `probe_all` below.


@dataclass(frozen=True)
class Probe:
    hostname: str
    system_answer: str  # first A record the system resolver returned
    public_answer: str  # first A record a public resolver returned
    hijacked: bool


def _dig(hostname: str, server: str | None = None, timeout: int = 3) -> str:
    """Return the first A record for `hostname` via `dig`, or ''."""
    return (_dig_all(hostname, server=server, timeout=timeout) or [""])[0]


def _dig_all(hostname: str, server: str | None = None, timeout: int = 3) -> list[str]:
    """Return every A record for `hostname` via `dig`, or []."""
    cmd = [
        "dig",
        "+short",
        "+tries=1",
        f"+time={timeout}",
        "A",
        hostname,
    ]
    if server:
        cmd.append(f"@{server}")
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, text=True, timeout=timeout + 2
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return []
    results: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(".")
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            results.append(line)
    return results


def _public_answers(hostname: str) -> list[str]:
    """Return the union of all routable answers from public resolvers."""
    out: set[str] = set()
    for resolver in PUBLIC_RESOLVERS:
        for ans in _dig_all(hostname, server=resolver):
            if ans and ans not in HIJACK_MARKERS:
                out.add(ans)
    return sorted(out)


def probe_all(hostnames: tuple[str, ...] = REQUIRED_HOSTS) -> list[Probe]:
    """Run the system-vs-public probe for every hostname.

    A hostname is HIJACKED when either:
      - the system answer is loopback / empty / null-route, OR
      - the system answer is routable but doesn't match ANY of the
        public resolvers' answers (covers ISP block pages that
        return a regulator's "blocked" landing IP).

    We pick the patch IP from the public-resolver set so later
    subscribers can re-run the probe and confirm.
    """
    results: list[Probe] = []
    for host in hostnames:
        system_answers = _dig_all(host)
        system = system_answers[0] if system_answers else ""
        public_set = _public_answers(host)
        public = public_set[0] if public_set else ""
        if not public:
            hijacked = False  # can't prove upstream without a known-good reference
        elif system in HIJACK_MARKERS:
            hijacked = True
        else:
            # Hijacked when no system answer is in the public-resolver set.
            hijacked = not any(a in public_set for a in system_answers)
        results.append(
            Probe(hostname=host, system_answer=system, public_answer=public, hijacked=hijacked)
        )
    return results


def _print_report(probes: list[Probe]) -> None:
    """Log a table; last line is a human-readable summary."""
    print(f"{'HOSTNAME':<40} {'SYSTEM':<16} {'PUBLIC':<16} STATUS")
    print("-" * 90)
    for p in probes:
        status = "HIJACKED" if p.hijacked else "ok"
        print(
            f"{p.hostname:<40} "
            f"{(p.system_answer or '(nxdomain)'):<16} "
            f"{(p.public_answer or '(nxdomain)'):<16} "
            f"{status}"
        )
    n_bad = sum(1 for p in probes if p.hijacked)
    if n_bad:
        print(f"\n{n_bad} hostname(s) need patching.")
    else:
        print("\nAll hostnames resolve correctly; no patch needed.")


def _render_block(probes: list[Probe]) -> str:
    """Build the managed `/etc/hosts` block for hijacked hosts."""
    lines = [MARKER_BEGIN]
    for p in probes:
        if p.hijacked:
            lines.append(f"{p.public_answer} {p.hostname}")
    lines.append(MARKER_END)
    return "\n".join(lines) + "\n"


def _strip_existing_block(contents: str) -> str:
    """Remove any prior managed block so the new one replaces it."""
    out_lines: list[str] = []
    inside = False
    for line in contents.splitlines():
        if line.strip() == MARKER_BEGIN:
            inside = True
            continue
        if line.strip() == MARKER_END:
            inside = False
            continue
        if inside:
            continue
        out_lines.append(line)
    # Preserve trailing newline behaviour.
    joined = "\n".join(out_lines)
    if contents.endswith("\n") and not joined.endswith("\n"):
        joined += "\n"
    return joined


def apply_patch(probes: list[Probe]) -> int:
    """Write the managed block into /etc/hosts. Requires root.

    The probe runs against the system resolver, which reads /etc/hosts
    first — so hostnames we've already patched look "ok" on subsequent
    runs. Naively rewriting the block to include only CURRENTLY-hijacked
    hostnames would strip previously-patched ones and re-expose them to
    the upstream hijack. So: we keep every hostname in REQUIRED_HOSTS
    for which a public resolver returns a routable answer, regardless
    of whether the current-run probe flagged it. The block self-heals
    (re-pins whatever the public resolvers currently return) and is
    idempotent.
    """
    if os.geteuid() != 0:
        print("dns-fix: re-exec under sudo to write /etc/hosts ...", file=sys.stderr)
        argv = ["sudo", sys.executable, *sys.argv]
        os.execvp("sudo", argv)
        return 1

    # Build the authoritative block: for every REQUIRED_HOSTS entry,
    # look up the public-resolver answer directly (not via the system
    # resolver, which reads /etc/hosts). If public resolution works,
    # pin it. If not, skip it — no routable upstream means we can't
    # pin anything useful.
    pinned: list[tuple[str, str]] = []
    for host in REQUIRED_HOSTS:
        public_set = _public_answers(host)
        if public_set:
            pinned.append((public_set[0], host))
        else:
            print(
                f"dns-fix: WARNING — no public-resolver answer for {host}; skipping",
                file=sys.stderr,
            )

    original = HOSTS_FILE.read_text()
    cleaned = _strip_existing_block(original)

    if not pinned:
        if cleaned != original:
            _atomic_write(HOSTS_FILE, cleaned)
            print("dns-fix: no public-resolver answers; removed stale managed block.")
        else:
            print("dns-fix: no public-resolver answers; nothing to do.")
        return 0

    block_lines = [MARKER_BEGIN]
    for ip, host in pinned:
        block_lines.append(f"{ip} {host}")
    block_lines.append(MARKER_END)
    new_block = "\n".join(block_lines) + "\n"

    if cleaned and not cleaned.endswith("\n"):
        cleaned += "\n"
    updated = cleaned + "\n" + new_block
    if updated == original:
        print("dns-fix: /etc/hosts already correct; no write needed.")
        return 0
    _atomic_write(HOSTS_FILE, updated)
    print(f"dns-fix: pinned {len(pinned)} hostname(s) in /etc/hosts.")
    return 0


def _atomic_write(target: Path, content: str) -> None:
    """Write to a tempfile in the target's dir, then rename over."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        delete=False,
        dir=str(target.parent),
        prefix=target.name + ".",
    ) as fh:
        fh.write(content)
        tmp = Path(fh.name)
    # Preserve permissions of the original.
    shutil.copymode(target, tmp)
    tmp.replace(target)


def revert() -> int:
    """Strip the managed block from /etc/hosts. Requires root."""
    if os.geteuid() != 0:
        print("dns-fix: re-exec under sudo to edit /etc/hosts ...", file=sys.stderr)
        os.execvp("sudo", ["sudo", sys.executable, *sys.argv])
        return 1
    original = HOSTS_FILE.read_text()
    cleaned = _strip_existing_block(original)
    if cleaned == original:
        print("dns-fix: no managed block present; nothing to revert.")
        return 0
    _atomic_write(HOSTS_FILE, cleaned)
    print("dns-fix: removed the managed block from /etc/hosts.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe + patch Polymarket DNS hijacks on this host."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Patch /etc/hosts if hijacks are detected (re-execs under sudo).",
    )
    parser.add_argument(
        "--revert",
        action="store_true",
        help="Remove the managed block from /etc/hosts.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the probe table; exit code still signals state.",
    )
    args = parser.parse_args(argv)

    if args.revert:
        return revert()

    probes = probe_all()

    if not args.quiet:
        _print_report(probes)

    if args.apply:
        return apply_patch(probes)

    # Dry-run: non-zero if at least one hijack is active so CI /
    # Makefile gates can trigger the fix.
    return 1 if any(p.hijacked for p in probes) else 0


if __name__ == "__main__":
    sys.exit(main())
