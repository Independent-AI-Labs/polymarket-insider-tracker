"""Public-facing subscription surface (Phase F).

Tiny FastAPI app carrying the three endpoints REQ-MAIL §10 mandates:
- POST /subscribe   — insert pending row, send confirmation email.
- GET  /opt-in      — flip pending -> active on valid token.
- GET  /unsubscribe — one-click opt-out via unsubscribe token.

The app is deliberately minimal: no auth, no user dashboard, just the
lifecycle transitions. Operators expose it via a Cloudflare Pages
static signup form that POSTs to this service, or a simple Next.js /
plain HTML page — neither of which is shipped in this repo.

See `projects/AMI-STREAMS/docs/SPEC-MAIL.md` §10 for the flow details.
"""

from polymarket_insider_tracker.web.app import create_app

__all__ = ["create_app"]
