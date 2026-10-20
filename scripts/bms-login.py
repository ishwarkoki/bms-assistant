#!/usr/bin/env python3
"""Log in to BookMyShow once, into the dedicated automation profile.

The profile lives in ``data/browser-profile/`` (gitignored). Session cookies
never leave this machine; the recon and auto-cart scripts reuse the profile.
No credentials are ever seen or stored by this script.

Usage:
    uv run python scripts/bms-login.py
"""

from __future__ import annotations

from bms_common import INSTALL_HINT, PROFILE_DIR, import_sync_playwright

START_URL = "https://in.bookmyshow.com/"


def main() -> int:
    sync_playwright = import_sync_playwright()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        try:
            context = pw.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                headless=False,
                viewport={"width": 1440, "height": 960},
            )
        except Exception as error:
            print(f"Could not start Chromium: {error}")
            print(INSTALL_HINT)
            return 1

        page = context.pages[0] if context.pages else context.new_page()
        page.goto(START_URL)

        print(f"Profile: {PROFILE_DIR}")
        print("Log in in the browser window (finish any OTP), then come back here.")
        try:
            input("Press Enter once you are logged in to close the browser... ")
        except (EOFError, KeyboardInterrupt):
            print()

        context.close()

    print("Session saved.")
    print("Next: uv run python scripts/bms-recon.py --url <showtimes-url>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
