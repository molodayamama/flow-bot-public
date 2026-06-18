"""Render the lead pain HTML dashboard to a PNG via Playwright (offline, local file)."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--html", default="lead_scan_runs/pain_dashboard.html")
    parser.add_argument("--png", default="lead_scan_runs/pain_dashboard.png")
    parser.add_argument("--width", type=int, default=1440)
    args = parser.parse_args(argv)

    html_path = Path(args.html).resolve()
    if not html_path.exists():
        print(f"error: {html_path} not found", file=sys.stderr)
        return 1
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("error: playwright not installed", file=sys.stderr)
        return 1

    png_path = Path(args.png).resolve()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": args.width, "height": 1200}, device_scale_factor=2)
        page.goto(html_path.as_uri())
        page.wait_for_timeout(400)
        page.screenshot(path=str(png_path), full_page=True)
        browser.close()
    print(f"png: {png_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
