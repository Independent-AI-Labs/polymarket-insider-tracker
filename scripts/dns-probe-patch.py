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
)

# Public resolvers used as ground truth. First one that answers wins.
PUBLIC_RESOLVERS: tuple[str, ...] = ("1.1.1.1", "8.8.8.8")

# Anything in this set means "the system resolver is lying about this
# hostname." Loopback + the IPv4 null route + empty answers.
HIJACK_MARKERS: frozenset[str] = frozenset({"", "0.0.0.0", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class Probe:
    hostname: str
    system_answer: str  # first A record the system resolver returned
    public_answer: str  # first A record a public resolver returned
    hijacked: bool


def _dig(hostname: str, server: str | None = None, timeout: int = 3) -> str:
    """Return the first A record for `hostname` via `dig`, or ''.

    `server=None` uses the system resolver. Errors / timeouts collapse
    to empty string — the caller treats that as "no routable answer."
    """
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
        return ""
    # `dig +short` can emit CNAME lines before A records; pick the
    # first line that parses as dotted-quad.
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(".")
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return line
    return ""


def _public_answer(hostname: str) -> str:
    """Return the first routable answer across PUBLIC_RESOLVERS, or ''."""
    for resolver in PUBLIC_RESOLVERS:
        ans = _dig(hostname, server=resolver)
        if ans and ans not in HIJACK_MARKERS:
            return ans
    return ""


def probe_all(hostnames: tuple[str, ...] = REQUIRED_HOSTS) -> list[Probe]:
    """Run the system-vs-public probe for every hostname."""
    results: list[Probe] = []
    for host in hostnames:
        system = _dig(host)
        public = _public_answer(host)
        hijacked = (
            system in HIJACK_MARKERS
            and public != ""
            and public not in HIJACK_MARKERS
        )
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
    """Write the managed block into /etc/hosts. Requires root."""
    if os.geteuid() != 0:
        # Re-exec under sudo so the operator gets the password prompt
        # once, rather than the script silently failing to write.
        print("dns-fix: re-exec under sudo to write /etc/hosts ...", file=sys.stderr)
        argv = ["sudo", sys.executable, *sys.argv]
        os.execvp("sudo", argv)
        # execvp does not return on success.
        return 1

    if not any(p.hijacked for p in probes):
        # Still strip any prior block — resolver may have been healed
        # upstream and the pin is no longer needed.
        original = HOSTS_FILE.read_text()
        cleaned = _strip_existing_block(original)
        if cleaned != original:
            _atomic_write(HOSTS_FILE, cleaned)
            print("dns-fix: no hijacks detected; removed stale managed block.")
        else:
            print("dns-fix: no hijacks, no managed block, nothing to do.")
        return 0

    original = HOSTS_FILE.read_text()
    cleaned = _strip_existing_block(original)
    new_block = _render_block(probes)
    if cleaned and not cleaned.endswith("\n"):
        cleaned += "\n"
    updated = cleaned + "\n" + new_block
    if updated == original:
        print("dns-fix: /etc/hosts already correct; no write needed.")
        return 0
    _atomic_write(HOSTS_FILE, updated)
    n = sum(1 for p in probes if p.hijacked)
    print(f"dns-fix: patched {n} hostname(s) in /etc/hosts.")
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
