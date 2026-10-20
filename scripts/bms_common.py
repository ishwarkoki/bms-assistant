"""Shared helpers for the opt-in BookMyShow browser scripts.

Only these scripts need the optional ``bms`` extra:

    uv sync --extra bms
    uv run playwright install chromium
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = PROJECT_ROOT / "data" / "browser-profile"
RECON_DIR = PROJECT_ROOT / "data" / "bms-recon"

INSTALL_HINT = (
    "Playwright is not ready. Run:\n  uv sync --extra bms\n  uv run playwright install chromium"
)


def _missing_playwright() -> None:
    print(INSTALL_HINT, file=sys.stderr)
    raise SystemExit(1)


def import_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        _missing_playwright()
    return sync_playwright


def import_async_playwright():
    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError:
        _missing_playwright()
    return async_playwright
