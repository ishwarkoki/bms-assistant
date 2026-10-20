#!/usr/bin/env python3
"""Capture BookMyShow traffic while you walk through to the seat map.

Records JSON XHR/fetch responses (headers and cookies are never captured),
screenshots every navigation, and lets you save extra screenshots or DOM
snapshots from this terminal. Everything lands in ``data/bms-recon/<stamp>/``,
which is gitignored. Key-name redaction is best effort: review before sharing.

Usage:
    uv run python scripts/bms-recon.py --url "https://in.bookmyshow.com/..."

Walkthrough once the browser opens:
    1. Let the showtimes page load.
    2. Open a real show and get to the seat-selection page; click a few seats.
    3. Stop at the payment-options screen and do not pay.
    4. Press q here to close the browser and write the capture index.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import threading
from datetime import datetime
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from bms_common import PROFILE_DIR, RECON_DIR, import_async_playwright

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page, Response

MAX_BODY_BYTES = 3 * 1024 * 1024
SENSITIVE_KEY = re.compile(
    r"(token|jwt|auth|password|otp|email|mobile|phone|card|upi|pin|session|cookie|csrf)",
    re.IGNORECASE,
)
SLUG = re.compile(r"[^a-zA-Z0-9]+")


def redact(value: Any) -> Any:
    """Replace values under sensitive-looking keys. Best effort, not a guarantee."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if isinstance(key, str) and SENSITIVE_KEY.search(key)
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def slugify(url: str) -> str:
    path = urlsplit(url).path.strip("/") or "root"
    slug = SLUG.sub("-", path)[:80].strip("-")
    return slug or "page"


def endpoint_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class Recorder:
    """Collects responses, screenshots, and DOM snapshots into one directory."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.json_dir = out_dir / "json"
        self.shots_dir = out_dir / "screenshots"
        self.dom_dir = out_dir / "dom"
        for directory in (self.json_dir, self.shots_dir, self.dom_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.index: list[dict[str, Any]] = []
        self._json_counter = count(1)
        self._shot_counter = count(1)
        self._dom_counter = count(1)

    async def on_response(self, response: Response) -> None:
        try:
            if response.request.resource_type not in {"xhr", "fetch"}:
                return
            headers = await response.all_headers()
            if "json" not in headers.get("content-type", "").lower():
                return
            body = await response.text()
            if len(body.encode("utf-8", errors="ignore")) > MAX_BODY_BYTES:
                self.index.append(
                    {"url": response.url, "status": response.status, "skipped": "body too large"}
                )
                return
            filename = f"{next(self._json_counter):04d}-{slugify(response.url)}.json"
            payload = {
                "url": response.url,
                "status": response.status,
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "data": redact(json.loads(body)),
            }
            (self.json_dir / filename).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self.index.append(
                {
                    "url": response.url,
                    "status": response.status,
                    "file": f"json/{filename}",
                    "bytes": len(body),
                }
            )
            print(f"saved json/{filename}")
        except Exception as error:
            self.index.append({"url": response.url, "error": str(error)})

    async def screenshot(self, page: Page, prefix: str = "shot") -> None:
        filename = f"{prefix}-{next(self._shot_counter):04d}.png"
        try:
            await page.screenshot(path=str(self.shots_dir / filename))
            print(f"saved screenshots/{filename}")
        except Exception as error:
            print(f"screenshot failed: {error}")

    async def dom(self, page: Page) -> None:
        filename = f"dom-{next(self._dom_counter):04d}.html"
        try:
            (self.dom_dir / filename).write_text(await page.content(), encoding="utf-8")
            print(f"saved dom/{filename}")
        except Exception as error:
            print(f"DOM snapshot failed: {error}")

    def write_index(self) -> None:
        index_file = self.out_dir / "index.json"
        index_file.write_text(json.dumps(self.index, indent=2), encoding="utf-8")

    def print_summary(self) -> None:
        captured = [entry for entry in self.index if "file" in entry]
        print(f"\nCaptured {len(captured)} JSON responses in {self.out_dir}")
        counts: dict[str, int] = {}
        for entry in captured:
            endpoint = endpoint_of(str(entry["url"]))
            counts[endpoint] = counts.get(endpoint, 0) + 1
        for endpoint, hits in sorted(counts.items(), key=lambda item: -item[1])[:15]:
            print(f"  {hits:>3}  {endpoint}")
        print("Next: share the index (not the whole capture) to plan the watcher.")


def print_commands() -> None:
    print("Commands: [s] screenshot  [d] DOM snapshot  [q] quit and save")


def start_stdin_reader(queue: asyncio.Queue[str | None], loop: asyncio.AbstractEventLoop) -> None:
    """Forward terminal lines into the event loop from a daemon thread."""

    def read() -> None:
        while True:
            line = sys.stdin.readline()
            loop.call_soon_threadsafe(queue.put_nowait, None if not line else line.rstrip("\n"))
            if not line:
                return

    threading.Thread(target=read, daemon=True).start()


async def command_loop(page: Page, recorder: Recorder) -> None:
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    start_stdin_reader(queue, asyncio.get_running_loop())

    while True:
        line = await queue.get()
        if line is None:  # EOF or Ctrl+D
            return
        command = line.strip().lower()
        if command in {"q", "quit", "exit"}:
            return
        if command in {"s", "shot", "screenshot"}:
            await recorder.screenshot(page)
        elif command in {"d", "dom", "dump"}:
            await recorder.dom(page)
        elif command:
            print_commands()


async def run(url: str, out_dir: Path) -> int:
    async_playwright = import_async_playwright()
    recorder = Recorder(out_dir)
    if not PROFILE_DIR.exists():
        print(f"Tip: no profile at {PROFILE_DIR} yet - run scripts/bms-login.py first.")

    async with async_playwright() as pw:
        try:
            context = await pw.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                headless=False,
                viewport={"width": 1440, "height": 960},
            )
        except Exception as error:
            print(f"Could not start Chromium: {error}")
            print("Run: uv run playwright install chromium")
            return 1

        try:
            page = context.pages[0] if context.pages else await context.new_page()
            page.on("response", recorder.on_response)

            async def on_navigation(frame: Frame) -> None:
                if frame == page.main_frame:
                    await recorder.screenshot(page, "nav")

            page.on("framenavigated", on_navigation)

            print(f"Capture directory: {out_dir}")
            print("Walk: let the page load, then showtimes -> seat map -> payment options.")
            print_commands()
            print("Headers and cookies are never captured. Redaction is best effort.")

            try:
                await page.goto(url)
            except Exception as error:
                print(f"Could not open {url}: {error}")
                print("You can navigate manually in the browser window.")

            await command_loop(page, recorder)
        finally:
            await context.close()
            recorder.write_index()
            recorder.print_summary()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture BMS network traffic for recon.")
    parser.add_argument(
        "--url",
        required=True,
        help="BookMyShow page to open first (a showtimes or event page)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="capture directory (default: data/bms-recon/<timestamp>)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.out.resolve() if args.out else RECON_DIR / stamp
    try:
        return asyncio.run(run(args.url, out_dir))
    except KeyboardInterrupt:
        print("\nInterrupted. The capture directory was kept for inspection.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
