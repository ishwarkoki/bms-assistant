#!/usr/bin/env bash
# Run the watcher on this machine without macOS idle sleep.
#
# Usage: ./scripts/run-local.sh
#
# A closed MacBook lid still sleeps the machine. Keep it open and plugged in
# while a drop is being watched, or run this on an always-on machine.

set -euo pipefail

cd "$(dirname "$0")/.."

if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i uv run python -m bms_assistant "$@"
fi

exec uv run python -m bms_assistant "$@"
