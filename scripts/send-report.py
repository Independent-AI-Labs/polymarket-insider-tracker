#!/usr/bin/env python3
"""Polymarket Report Generator & Mailer — YAML-driven, template-based.

Usage:
    python3 scripts/send-report.py                          # defaults from config
    python3 scripts/send-report.py --date 2026-04-05        # specific date
    python3 scripts/send-report.py --config my-config.yaml  # custom config
    python3 scripts/send-report.py --no-send                # generate only, skip email
    python3 scripts/send-report.py --targets vlad,archive   # specific targets only
    python3 scripts/send-report.py --dry-run                # print what would be sent
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "report-config.yaml"
TEMPLATES_DIR = SCRIPT_DIR / "templates"
NEWSLETTER_TEMPLATE = TEMPLATES_DIR / "polymarket-newsletter.html"

# Match a paired `**…**` markdown-bold span — Python renders these into
# <strong>…</strong> inside build_targets_data so the Tera template can
# emit the observation with `| safe` and stay straight iterator logic.
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


# ── Data fetching ────────────────────────────────────────────────────────────


def fetch_json(url: str, cfg: dict) -> list | dict:
    api_cfg = cfg.get("api", {})
    req = urllib.request.Request(
        url,
        headers={"User-Agent": api_cfg.get("user_agent", "AMI-Reports/1.0")},
    )
    timeout = api_cfg.get("timeout_seconds", 30)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _market_is_live(raw: dict) -> bool:
    """Gamma sometimes flags past-end-date markets as active=true. The
    Hezbollah ceasefire market kept showing in "near-certain" two days
    after it ended because of this. Belt-and-braces: exclude anything
    with closed=true OR endDate already in the past.
    """
    if bool(raw.get("closed")) is True:
        return False
    end_raw = str(raw.get("endDate", "") or raw.get("endDateIso", "") or "")
    if not end_raw:
        return True
    try:
        end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    return end_dt > datetime.now(timezone.utc)


def fetch_sections(cfg: dict) -> list[dict]:
    """Fetch market data for each configured section."""
    base_url = cfg["api"]["base_url"]
    sections = []

    for section_cfg in cfg["sections"]:
        params = {
            # Over-fetch, then post-filter to `limit` live markets, so
            # past-end-date stragglers don't steal slots from real ones.
            "limit": section_cfg.get("limit", 20) * 2,
            "order": section_cfg["order"],
            "ascending": str(section_cfg.get("ascending", False)).lower(),
            "active": str(section_cfg.get("active", True)).lower(),
        }
        if "closed" in section_cfg:
            params["closed"] = str(section_cfg["closed"]).lower()

        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{base_url}/markets?{query}"

        print(f"  Fetching: {section_cfg['title']} ({section_cfg.get('limit', 20)} markets)...")
        raw_markets = fetch_json(url, cfg)

        # Format each market row according to column config
        target_limit = section_cfg.get("limit", 20)
        markets = []
        for m in raw_markets:
            if not _market_is_live(m):
                continue
            row = format_market_row(m, cfg["columns"])
            row["_raw"] = m  # keep raw data for observation rules
            markets.append(row)
            if len(markets) >= target_limit:
                break

        sections.append({
            "title": section_cfg["title"],
            "markets": markets,
            "config": section_cfg,
        })

    return sections


# ── Formatting ───────────────────────────────────────────────────────────────


def fmt_usd(val: float) -> str:
    if val >= 1_000_000:
        return f"${val / 1_000_000:.2f}M"
    if val >= 1_000:
        return f"${val / 1_000:.1f}K"
    return f"${val:,.0f}"


def format_market_row(m: dict, columns: list[dict]) -> dict:
    """Format a raw market dict into display values per column config."""
    row: dict = {}
    for col in columns:
        field = col["field"]
        fmt = col.get("format", "text")
        header = col["header"]

        if fmt == "usd":
            val = float(m.get(field, 0) or 0)
            row[header] = fmt_usd(val)
        elif fmt == "bid_ask":
            parts = field.split("/")
            bid = m.get(parts[0], "—") or "—"
            ask = m.get(parts[1], "—") if len(parts) > 1 else "—"
            row[header] = f"{bid}/{ask}"
        elif fmt == "date":
            raw = m.get(field) or "—"
            row[header] = raw[:10] if raw != "—" else "—"
        else:  # text
            val = m.get(field, "N/A") or "N/A"
            max_w = col.get("max_width")
            if max_w and len(str(val)) > max_w:
                val = str(val)[: max_w - 3] + "..."
            # Hyperlink the question column to the market page so the
            # PDF readers' clicks land somewhere useful. `slug` is on
            # every gamma-api market response.
            if field == "question":
                slug = m.get("slug") or ""
                if slug:
                    # Escape pipes + brackets that would break the markdown table.
                    safe = str(val).replace("|", "\\|").replace("[", "(").replace("]", ")")
                    row[header] = f"[{safe}](https://polymarket.com/event/{slug})"
                else:
                    row[header] = str(val)
            else:
                row[header] = str(val)

    return row


# ── Observations ─────────────────────────────────────────────────────────────


def generate_observations(sections: list[dict], cfg: dict) -> list[str]:
    """Auto-detect interesting patterns.

    Phase S0 retired the `thin_book` and `near_certain` rules per
    docs/SPEC-MARKET-SIGNALS.md § 6. The signal-led daily
    (scripts/newsletter-daily.py) carries the real signals; the
    PDF generated from this file is a vestigial 24 h volume
    snapshot until Phase S3 replaces it with the Option-B
    flagged-activity-log appendix.
    """
    return []


# ── Summary stats ────────────────────────────────────────────────────────────


def compute_stats(sections: list[dict]) -> dict[str, str]:
    """Compute summary stats from fetched sections."""
    stats: dict[str, str] = {}

    if len(sections) > 0:
        vol_markets = sections[0]["markets"]
        n = len(vol_markets)
        total_24h = sum(float(m["_raw"].get("volume24hr", 0) or 0) for m in vol_markets)
        total_vol = sum(float(m["_raw"].get("volume", 0) or 0) for m in vol_markets)
        total_liq = sum(float(m["_raw"].get("liquidityClob", 0) or 0) for m in vol_markets)
        stats[f"24h volume (top {n} markets)"] = fmt_usd(total_24h)
        stats[f"All-time volume (top {n} markets)"] = fmt_usd(total_vol)
        stats[f"Book liquidity (top {n} markets)"] = fmt_usd(total_liq)

    return stats


# ── Template rendering ───────────────────────────────────────────────────────


def render_template(template_name: str, ctx: dict, templates_dir: Path) -> str:
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    tmpl = env.get_template(template_name)
    return tmpl.render(**ctx)


# ── PDF conversion ───────────────────────────────────────────────────────────


def convert_to_pdf(md_path: Path, pdf_path: Path, cfg: dict) -> None:
    """Markdown → PDF.

    Earlier attempts went through pandoc's default wkhtmltopdf path,
    which wraps the content in a verbose HTML template (big title
    block, generous body padding, no table-header repetition on page
    breaks). The padding was what made the PDF look like a letter on
    A3 paper and column headers got re-drawn per page — except without
    `thead-as-header-group` the first row of each new page overlapped
    where the header SHOULD have been.

    This path renders markdown → HTML fragment via pandoc, wraps it
    in a minimal HTML shell with our own CSS (including the critical
    `thead { display: table-header-group }` that tells wkhtmltopdf to
    repeat headers on every page), and hands the HTML straight to
    wkhtmltopdf with explicit `--margin-*` args.
    """
    pdf_cfg = cfg.get("pdf", {})
    margins = pdf_cfg.get("margins", {})

    # 1. Markdown → HTML fragment. `--to html5` without `-s` (standalone)
    #    gives us just the body content with no template wrapping.
    frag_result = subprocess.run(
        ["pandoc", str(md_path), "--to", "html5"],
        capture_output=True, text=True,
    )
    if frag_result.returncode != 0:
        raise RuntimeError(f"pandoc html fragment failed: {frag_result.stderr}")
    html_fragment = frag_result.stdout

    # 2. Wrap with minimal shell + CSS.
    css = """
      * { box-sizing: border-box; }
      html, body { margin: 0; padding: 0; }
      body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 10pt;
        color: #111;
        line-height: 1.4;
      }
      h1 { font-size: 18pt; margin: 0 0 8pt; }
      h2 { font-size: 13pt; margin: 14pt 0 6pt; border-bottom: 1px solid #ccc; padding-bottom: 3pt; }
      p { margin: 4pt 0; }
      em { color: #666; font-style: italic; }
      a { color: #1a5fb4; text-decoration: none; }
      ul { margin: 4pt 0 4pt 18pt; padding: 0; }
      li { margin: 2pt 0; }
      table { width: 100%; border-collapse: collapse; margin: 6pt 0 10pt; font-size: 9pt; }
      th, td { padding: 4pt 6pt; border-bottom: 1px solid #ddd; text-align: left; }
      th { background: #f4f4f4; font-weight: 600; color: #333; }
      td { color: #222; }
      /* Repeat table headers across page breaks — this is the fix
         for "row text overlapping column headers on new pages". */
      thead { display: table-header-group; }
      tr, td, th { page-break-inside: avoid; }
      tr { page-break-after: auto; }
    """.strip()

    html_doc = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>{css}</style></head>
<body>
{html_fragment}
</body>
</html>
"""

    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", prefix="snapshot-", delete=False
    ) as fh:
        fh.write(html_doc)
        html_path = Path(fh.name)

    try:
        cmd = [
            "wkhtmltopdf",
            "--quiet",
            "--enable-local-file-access",
            "--margin-top", f"{margins.get('top', 8)}mm",
            "--margin-bottom", f"{margins.get('bottom', 8)}mm",
            "--margin-left", f"{margins.get('left', 8)}mm",
            "--margin-right", f"{margins.get('right', 8)}mm",
            "--page-size", "A4",
            "--encoding", "utf-8",
            str(html_path),
            str(pdf_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"wkhtmltopdf failed (rc={result.returncode}): "
                f"{result.stderr or result.stdout}"
            )
    finally:
        html_path.unlink(missing_ok=True)


# ── Email delivery (via himalaya batch send) ─────────────────────────────────


def _strip_raw(sections: list[dict]) -> list[dict]:
    """Drop the `_raw` Polymarket blobs before they reach the YAML data file.

    They are only needed for the Python-side observation rules and would
    balloon the per-recipient payload by ~100x.
    """
    out: list[dict] = []
    for s in sections:
        markets = [{k: v for k, v in m.items() if k != "_raw"} for m in s["markets"]]
        out.append({
            "title": s["title"],
            "markets": markets,
            "market_count": len(markets),
        })
    return out


def filter_targets(targets: list[dict], names: str | None) -> list[dict]:
    """Narrow the recipient list by `--targets a,b,c` or enabled flag."""
    if names:
        requested = {t.strip() for t in names.split(",")}
        return [t for t in targets if t["name"] in requested]
    return [t for t in targets if t.get("enabled", True)]


def build_targets_data(
    cfg: dict,
    targets: list[dict],
    base_ctx: dict,
) -> list[dict]:
    """Produce the list of rows himalaya batch send will iterate over.

    Each row carries the recipient address, a per-recipient subject, and a
    nested ``report`` context the Tera template consumes directly.
    """
    email_cfg = cfg["email"]
    summary_top_n = email_cfg.get("summary_top_n", 5)
    max_obs = email_cfg.get("max_observations", 5)
    default_subject_tpl = email_cfg["subject_template"]

    sections_clean = _strip_raw(base_ctx["sections"])
    summary_markets = (
        sections_clean[0]["markets"][:summary_top_n] if sections_clean else []
    )
    extra_sections = sections_clean[1:] if len(sections_clean) > 1 else []
    observations_html = [
        _BOLD_RE.sub(r"<strong>\1</strong>", o)
        for o in base_ctx["observations"][:max_obs]
    ]

    report = {
        "date": base_ctx["date"],
        "generated": base_ctx["generated"],
        "title": base_ctx["title"],
        "stats": base_ctx["stats"],
        "sections": sections_clean,
        "summary_markets": summary_markets,
        "extra_sections": extra_sections,
        "observations": observations_html,
        "config": cfg,
    }

    rows: list[dict] = []
    for t in targets:
        subject_tpl = t.get("subject_template", default_subject_tpl)
        subject = subject_tpl.format(date=base_ctx["date"])
        rows.append({
            "email": t["email"],
            "name": t.get("name", t["email"]),
            "subject": subject,
            "report": report,
        })
    return rows


def deliver_via_himalaya(
    rows: list[dict],
    pdf_path: Path,
    cfg: dict,
    template_path: Path,
    dry_run: bool,
) -> int:
    """Invoke `himalaya batch send` with a temp YAML data file. Returns rc."""
    if not rows:
        print("  [WARN] No delivery targets matched")
        return 0

    delivery = cfg.get("delivery", {})
    account = delivery.get("account", "polymarket")
    rate = delivery.get("rate", "5/min")

    # himalaya's individual-mode subject is a Tera template rendered per row;
    # our row already carries the final string under `subject`, so the template
    # is just `{{ subject }}`.
    subject_template = "{{ subject }}"

    # NamedTemporaryFile unlinks on context exit — covers both success and
    # exception paths without hand-rolled cleanup.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="polymarket-targets-",
    ) as fh:
        yaml.safe_dump(rows, fh, allow_unicode=True, sort_keys=False)
        fh.flush()
        data_path = fh.name

        cmd = [
            "himalaya", "batch", "send",
            "--account", account,
            "--template", str(template_path),
            "--data", data_path,
            "--subject", subject_template,
            "--attachment", str(pdf_path),
            "--rate", rate,
            "--yes",
        ]
        if dry_run:
            cmd.append("--dry-run")

        print(f"  → himalaya batch send → {len(rows)} recipient(s) via account '{account}'")
        for r in rows:
            print(f"    • {r['email']}  subject={r['subject']!r}")

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


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Polymarket Report Generator & Mailer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", "-c",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"YAML config file (default: {DEFAULT_CONFIG.name})",
    )
    parser.add_argument("--date", "-d", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--no-send", action="store_true", help="Generate report only, skip email")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be sent")
    parser.add_argument("--targets", help="Comma-separated target names (default: all enabled)")
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=TEMPLATES_DIR,
        help=f"Templates directory (default: {TEMPLATES_DIR})",
    )
    args = parser.parse_args()

    # Load config
    cfg = yaml.safe_load(args.config.read_text())
    date = args.date
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    # Resolve output paths
    project_dir = SCRIPT_DIR.parent
    out_dir = project_dir / cfg["output"]["dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / cfg["output"]["markdown"].format(date=date)
    pdf_path = out_dir / cfg["output"]["pdf"].format(date=date)

    # Step 1: Fetch data
    print(f"[1/4] Fetching market data...")
    sections = fetch_sections(cfg)

    # Step 2: Build context
    stats = compute_stats(sections)
    observations = generate_observations(sections, cfg)

    title = cfg["email"]["subject_template"].replace("{date}", date).replace("[AMI] ", "")

    ctx = {
        "date": date,
        "generated": generated,
        "title": title,
        "stats": stats,
        "sections": sections,
        "observations": observations,
        "config": cfg,
    }

    # Step 3: Render markdown report
    print(f"[2/4] Generating report...")
    md_content = render_template("report.md.jinja2", ctx, args.templates_dir)
    md_path.write_text(md_content)
    print(f"  → {md_path}")

    # Step 4: Convert to PDF (attached to every email)
    print(f"[3/4] Converting to PDF...")
    convert_to_pdf(md_path, pdf_path, cfg)
    size = pdf_path.stat().st_size
    print(f"  → {pdf_path} ({size // 1024}K)")

    if args.no_send:
        print("\n[SKIP] --no-send flag set, skipping email delivery")
        return

    # Step 5: Deliver via himalaya batch send (Tera renders the HTML per row)
    print(f"[4/4] Delivering...")
    targets = filter_targets(cfg["delivery"]["targets"], args.targets)
    rows = build_targets_data(cfg, targets, ctx)
    rc = deliver_via_himalaya(
        rows=rows,
        pdf_path=pdf_path,
        cfg=cfg,
        template_path=args.templates_dir / NEWSLETTER_TEMPLATE.name,
        dry_run=args.dry_run,
    )
    if rc != 0:
        raise SystemExit(rc)

    print(f"\nDone. Delivered to {len(rows)} target(s).")


if __name__ == "__main__":
    main()
