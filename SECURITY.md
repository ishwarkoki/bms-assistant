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
- CI runs Gitleaks against the full git history on every push and pull request.
- GitHub secret scanning and push protection are enabled for the repository.

If a secret is committed, **rotate it immediately**. Removing it in a later
commit does not remove it from git history or from any forks, caches, or alerts.
