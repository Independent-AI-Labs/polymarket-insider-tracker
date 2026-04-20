"""Email-safe icon + blockie generators.

Two modes:

- **CID mode** (email, default): renders each icon as a PNG to a
  per-process temp directory once, returns `(cid, filepath)`
  pairs. Every `<img>` tag in the email references `cid:<name>`
  and the MIME structure carries one `image/png` part per
  distinct image — industry-standard, survives Gmail web / ci3
  proxy / corporate DLP / every client since 1996.

- **Data-URI mode** (PDF): embeds the PNG as a base64 data-URI
  in the HTML. wkhtmltopdf consumes these just fine and the PDF
  has no MIME structure to worry about.

A `render_mode` context switch at the module level determines
which the current render pass is producing; `newsletter-daily.py`
sets it before invoking composer / pdf renderer.

Every image used during a render is registered in
`_used_cids` — a module-level set — so the newsletter script can
collect the MML `<#part>` directives it needs to emit.
"""

from __future__ import annotations

import base64
import hashlib
import io
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw


ICON_SIZE = 28   # Render at 2× the display size for retina sharpness.
DISPLAY_SIZE = 14


# ── Render-mode switch + CID registry ────────────────────────────────

RenderMode = Literal["cid", "data_uri"]
_current_mode: RenderMode = "data_uri"

# CID name → absolute file path. Populated by _png_to_path as
# images are requested during a render.
_cid_to_path: dict[str, str] = {}

# CIDs actually referenced during the current render pass.
# Reset by `reset_render_pass()` at the start of each send.
_used_cids: set[str] = set()


def set_render_mode(mode: RenderMode) -> None:
    """Flip the module between email (cid) and PDF (data_uri) modes."""
    global _current_mode
    _current_mode = mode


def reset_render_pass() -> None:
    """Clear the set of CIDs used in the current render."""
    _used_cids.clear()


def used_cid_parts() -> list[tuple[str, str]]:
    """Return `(cid, filepath)` for every image that was emitted
    during the current render. Consumer builds MML `<#part>` blocks
    from this list.
    """
    return [(cid, _cid_to_path[cid]) for cid in sorted(_used_cids)]


def _temp_dir() -> Path:
    """Per-process cache dir for rendered PNGs."""
    d = Path(tempfile.gettempdir()) / "polymarket-insider-icons"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pil_to_data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _pil_to_path(img: Image.Image, cid: str) -> str:
    """Persist the PNG to disk under a deterministic filename keyed on
    the CID. Returns the absolute file path.
    """
    path = _temp_dir() / f"{cid}.png"
    if not path.exists():
        img.save(path, format="PNG", optimize=True)
    return str(path)


def _emit_image_src(img: Image.Image, cid: str) -> str:
    """Depending on render mode: a `cid:X` ref or a data-URI string."""
    if _current_mode == "cid":
        path = _pil_to_path(img, cid)
        _cid_to_path[cid] = path
        _used_cids.add(cid)
        return f"cid:{cid}"
    return _pil_to_data_uri(img)


# ── Category icons — pixel-drawn, one per category ──────────────────


def _icon_radar(fg: str, bg: str) -> Image.Image:
    """Informed flow — concentric circles with a dot."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = ICON_SIZE // 2, ICON_SIZE // 2
    d.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], outline=fg, width=2)
    d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], outline=fg, width=2)
    d.line([cx, cy, cx + 10, cy - 10], fill=fg, width=2)
    d.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=fg)
    return img


def _icon_activity(fg: str, bg: str) -> Image.Image:
    """Microstructure — pulse line."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # EKG-style pulse — trending across the middle.
    points = [
        (2, 14), (7, 14), (9, 6), (13, 22), (17, 4), (20, 14), (26, 14),
    ]
    for a, b in zip(points[:-1], points[1:]):
        d.line([a, b], fill=fg, width=2)
    return img


def _icon_droplet(fg: str, bg: str) -> Image.Image:
    """Volume / liquidity — droplet."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Triangle top + circle bottom approximation of a droplet.
    d.polygon(
        [(14, 3), (5, 18), (23, 18)],
        fill=fg,
    )
    d.ellipse([5, 12, 23, 26], fill=fg)
    return img


def _icon_trending_up(fg: str, bg: str) -> Image.Image:
    """Price dynamics — arrow up-and-right."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Shaft
    d.line([(3, 22), (11, 14), (17, 18), (25, 6)], fill=fg, width=2)
    # Arrowhead
    d.polygon(
        [(25, 6), (17, 6), (25, 14)],
        fill=fg,
    )
    return img


def _icon_calendar(fg: str, bg: str) -> Image.Image:
    """Event catalyst — calendar grid."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 6, 24, 24], radius=2, outline=fg, width=2)
    d.line([(4, 11), (24, 11)], fill=fg, width=2)
    # Hanging tabs
    d.rectangle([9, 3, 11, 8], fill=fg)
    d.rectangle([17, 3, 19, 8], fill=fg)
    return img


def _icon_link(fg: str, bg: str) -> Image.Image:
    """Cross-market — two linked rounded rects."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Left chain link
    d.rounded_rectangle([3, 10, 15, 18], radius=4, outline=fg, width=2)
    # Right chain link
    d.rounded_rectangle([13, 10, 25, 18], radius=4, outline=fg, width=2)
    # Crossbar between
    d.line([(10, 14), (18, 14)], fill=fg, width=2)
    return img


_ICON_BUILDERS = {
    "informed_flow":   _icon_radar,
    "microstructure":  _icon_activity,
    "volume_liquidity": _icon_droplet,
    "price_dynamics":  _icon_trending_up,
    "event_catalyst":  _icon_calendar,
    "cross_market":    _icon_link,
}


# Cache the rendered PIL images per (category, fg) so we build them
# once per process. The cache does NOT encode render-mode — the mode
# only affects how we SERIALISE (path vs data-URI).
@lru_cache(maxsize=64)
def _rendered_icon(category: str, fg_hex: str) -> Image.Image | None:
    builder = _ICON_BUILDERS.get(category)
    if builder is None:
        return None
    return builder(fg_hex, None)


def category_icon_src(category: str, fg_hex: str) -> str:
    """Return a src value for the category icon — `cid:...` or
    `data:...;base64,...` per the module's current render mode.
    """
    img = _rendered_icon(category, fg_hex)
    if img is None:
        return ""
    # CID name keyed on category AND colour so a tinted variant is a
    # distinct MIME part — clean, no per-run collisions.
    colour_slug = fg_hex.lstrip("#").lower()
    cid = f"icon-{category}-{colour_slug}"
    return _emit_image_src(img, cid)


def category_icon_data_uri(category: str, fg_hex: str) -> str:
    """Force data-URI render regardless of current mode. Used by the
    PDF path so it doesn't flip the shared mode flag.
    """
    img = _rendered_icon(category, fg_hex)
    if img is None:
        return ""
    return _pil_to_data_uri(img)


# ── Blockie identicons (Ethereum-style) ──────────────────────────────


# Curated two-colour palettes. First byte of the SHA-256 hash (mod 16)
# picks one — so each address gets a stable colour identity.
_BLOCKIE_PALETTE: tuple[tuple[str, str], ...] = (
    ("#e0e7ff", "#3730a3"), ("#fee2e2", "#991b1b"),
    ("#ecfccb", "#3f6212"), ("#fef3c7", "#92400e"),
    ("#cffafe", "#155e75"), ("#fce7f3", "#9d174d"),
    ("#d1fae5", "#065f46"), ("#e0f2fe", "#075985"),
    ("#f3e8ff", "#6b21a8"), ("#fed7aa", "#9a3412"),
    ("#ccfbf1", "#115e59"), ("#dbeafe", "#1e40af"),
    ("#fde68a", "#854d0e"), ("#fbcfe8", "#831843"),
    ("#bae6fd", "#0c4a6e"), ("#e7e5e4", "#44403c"),
)


@lru_cache(maxsize=4096)
def _rendered_blockie(address: str) -> Image.Image | None:
    if not address:
        return None
    addr = address.lower()
    if addr.startswith("0x"):
        addr = addr[2:]
    digest = hashlib.sha256(addr.encode("ascii")).digest()
    palette_idx = digest[0] & 0x0F
    bg, fg = _BLOCKIE_PALETTE[palette_idx]
    cells: list[list[int]] = [[0] * 5 for _ in range(5)]
    bits = (digest[1] << 8) | digest[2]
    bit_idx = 0
    for row in range(5):
        for col in range(3):
            if bits & (1 << bit_idx):
                cells[row][col] = 1
            bit_idx += 1
    for row in range(5):
        cells[row][4] = cells[row][0]
        cells[row][3] = cells[row][1]
    cell_px = 5
    grid_px = cell_px * 5
    img = Image.new("RGB", (grid_px, grid_px), bg)
    d = ImageDraw.Draw(img)
    for row in range(5):
        for col in range(5):
            if cells[row][col]:
                x0 = col * cell_px
                y0 = row * cell_px
                d.rectangle(
                    [x0, y0, x0 + cell_px - 1, y0 + cell_px - 1],
                    fill=fg,
                )
    return img


def blockie_src(address: str) -> str:
    """Mode-aware blockie src — `cid:blockie-<addr>` or data-URI."""
    img = _rendered_blockie(address)
    if img is None:
        return ""
    addr_slug = address.lower().lstrip("0x")
    cid = f"blockie-{addr_slug}"
    return _emit_image_src(img, cid)


def blockie_data_uri(address: str) -> str:
    """Force data-URI render regardless of mode. Used by PDF path."""
    img = _rendered_blockie(address)
    if img is None:
        return ""
    return _pil_to_data_uri(img)
