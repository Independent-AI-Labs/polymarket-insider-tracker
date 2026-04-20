"""Signal registry — the single source of truth for what ships.

Adding a signal: import it here + append to REGISTRY. The composer
iterates the list in order; display order in the newsletter
mirrors registration order, which is the SPEC-MARKET-SIGNALS § 3
taxonomy sequence.

Retiring a signal: comment it out + update SPEC-MARKET-SIGNALS
audit log. The composer doesn't care.
"""

from __future__ import annotations

from .base import Signal
from .fresh_wallet import FreshWalletSignal
from .order_flow_imbalance import OrderFlowImbalanceSignal
from .stealth_cluster import StealthClusterSignal
from .unusual_size import UnusualSizeSignal
from .volume_velocity import VolumeVelocitySignal


# P0 signals — ship in the first production daily.
REGISTRY: list[Signal] = [
    FreshWalletSignal(),
    UnusualSizeSignal(),
    OrderFlowImbalanceSignal(),
    StealthClusterSignal(),
    VolumeVelocitySignal(),
    # 03-D thin-book gate is imported directly by signals that
    # need it — not rendered as a section. See thin_book_gate.py.
]


# Category display order for the newsletter (matches
# docs/SPEC-MARKET-SIGNALS.md § 3).
CATEGORY_ORDER: list[str] = [
    "informed_flow",
    "microstructure",
    "volume_liquidity",
    "price_dynamics",
    "event_catalyst",
    "cross_market",
]
