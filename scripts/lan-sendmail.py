#!/usr/bin/env python3
"""sendmail-compatible shim for the LAN exim-relay.

Himalaya's SMTP backend requires an auth stanza, but our LAN relay
accepts submissions without AUTH. himalaya's `sendmail` backend
solves this — it pipes the composed MIME message into an external
binary, letting us talk to the relay directly without going through
himalaya's own SMTP client.

Usage:
    lan-sendmail.py [-t] [-f from_addr] [-i] recipient1 [recipient2 ...] < message.eml

Accepts the subset of sendmail flags himalaya emits:
  -t           read recipients from To/Cc/Bcc headers
  -f ADDR      envelope From
  -i           don't treat lines starting with `.` as end-of-message
  --           end-of-flags

Reads the RFC 5322 message on stdin, opens an SMTP session to the
relay at SMTP_HOST:SMTP_PORT (env-configurable; defaults to the
project's LAN relay), runs HELO/MAIL/RCPT/DATA without AUTH, and
exits non-zero on delivery failure.
"""

from __future__ import annotations

import argparse
import email
import os
import smtplib
import sys
from email.policy import default as default_policy


SMTP_HOST = os.environ.get("LAN_SENDMAIL_HOST", "192.168.50.66")
SMTP_PORT = int(os.environ.get("LAN_SENDMAIL_PORT", "2526"))


def _collect_recipients_from_headers(msg: email.message.Message) -> list[str]:
    rcpts: list[str] = []
    for header in ("To", "Cc", "Bcc"):
        values = msg.get_all(header) or []
        for v in values:
            for addr in email.utils.getaddresses([v]):
                if addr[1]:
                    rcpts.append(addr[1])
    return rcpts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-t", action="store_true", dest="from_headers")
    parser.add_argument("-f", dest="from_addr", default=None)
    parser.add_argument("-i", action="store_true")  # accepted and ignored
    parser.add_argument("recipients", nargs="*")
    args, _unknown = parser.parse_known_args(argv)

    raw = sys.stdin.buffer.read()
    msg = email.message_from_bytes(raw, policy=default_policy)

    envelope_from = args.from_addr or msg.get("From") or "ami-reports@ami.local"
    envelope_from = email.utils.parseaddr(envelope_from)[1] or envelope_from

    rcpts = list(args.recipients)
    if args.from_headers:
        rcpts.extend(_collect_recipients_from_headers(msg))
    # Deduplicate while preserving order.
    seen: set[str] = set()
    rcpts = [r for r in rcpts if not (r in seen or seen.add(r))]
    if not rcpts:
        print("lan-sendmail: no recipients", file=sys.stderr)
        return 1

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo("ami-reports.local")
            smtp.sendmail(envelope_from, rcpts, raw)
    except (smtplib.SMTPException, OSError) as exc:
        print(f"lan-sendmail: delivery failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
