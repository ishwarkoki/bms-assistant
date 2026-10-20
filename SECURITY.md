# Security policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository
(**Security → Report a vulnerability**) rather than opening a public issue.
Include the affected version, reproduction steps, and impact.

## Secrets policy

- Real credentials belong in `.env` (gitignored) or the process environment.
- `.env.example` is the only env file that may be committed.
- The pre-commit suite runs Gitleaks, `detect-private-key`, and a file guard
  that blocks `.env`, local `data/`, databases, and key material.
- The Playwright browser profile in `data/browser-profile/` contains live
  session cookies. Treat it like a password; it is gitignored and blocked by
  the guard.
- Payment credentials are out of scope. Do not add card numbers, expiry dates,
  CVV/CVC, UPI IDs, UPI PINs, OTPs, or bank passwords to jobs, logs, fixtures,
  screenshots, or environment files. A UPI ID alone cannot authorize a payment;
  the PIN/biometric approval belongs in the user's PSP app.
- CI runs Gitleaks against the full git history on every push and pull request.
- GitHub secret scanning and push protection are enabled for the repository.
- Public deployments must set `BMS_AUTH_PASSWORD` and, when Telegram is enabled,
  `BMS_TELEGRAM_CHAT_ID`. `BMS_ENVIRONMENT=production` refuses to start
  without them.

If a secret is committed, **rotate it immediately**. Removing it in a later
commit does not remove it from git history or from any forks, caches, or alerts.
