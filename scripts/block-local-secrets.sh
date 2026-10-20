#!/usr/bin/env bash
# Refuse to stage files that should never enter a public repository: .env files,
# anything under data/ (SQLite database, Playwright browser profile with session
# cookies, recon captures), and key material.

set -euo pipefail

staged=$(git diff --cached --name-only --diff-filter=ACM)
if [[ -z "${staged}" ]]; then
  exit 0
fi

blocked=$(
  printf '%s\n' "${staged}" \
    | grep -E '(^|/)\.env($|\.)|(^|/)data/|(^|/)secrets/|(^|/)(id_rsa|id_ed25519)$|(^|/)(\.pypirc|\.netrc|\.npmrc|credentials\.json|secrets\.json|service[-_]?account.*\.json)$|\.(db|db-shm|db-wal|sqlite|pem|key|p12|pfx|jks)$' \
    | grep -vE '(^|/)\.env\.example$' \
    || true
)

if [[ -n "${blocked}" ]]; then
  echo "Secret guard: refusing to commit local secrets or data:"
  printf '  %s\n' "${blocked}"
  echo
  echo "Keep real values in .env (gitignored) and document placeholders in .env.example."
  exit 1
fi
