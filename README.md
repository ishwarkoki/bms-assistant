# Ticket Watch

A local-first ticket **watchlist** with a web dashboard and a Telegram bot. It watches your drop window, alerts you loudly when it's time to check BMS, and hands you the booking page. You choose the seats and approve the payment.

An **opt-in** auto-cart module is being added incrementally: browser automation that can score, select, and temporarily hold seats, then stop at the payment screen and wait for you. See [BMS auto-cart (opt-in)](#bms-auto-cart-opt-in).

It never completes a payment. There is no anti-bot evasion, CAPTCHA solving, stored card/UPI data, or automated OTP handling — and that will not change.

## What it does

- Create watches from the web UI or Telegram.
- Paste a BookMyShow link to auto-fill the movie/event code, venue code, city, and show date.
- Watch an exact showtime, a showtime window, or a whole date — including shows that are not on sale yet.
- Poll on a per-watch interval with jitter.
- Persist jobs and activity events in SQLite.
- Alert via the dashboard (SSE + sound), Telegram, and logs.
- Hand off to the booking URL and mark a watch as booked.
- Survive restarts: watches that were monitoring resume automatically.
- Mock provider for end-to-end development and a scheduled-reminder provider for real use.

## Decisions

| Area | Choice | Why |
| --- | --- | --- |
| Runtime | Python 3.12 + asyncio | Simple orchestration, first-class Telegram and web support |
| Package manager | `uv` | Fast, reproducible environments and lockfile |
| Web | FastAPI + Jinja + vanilla JS | No SPA build step; server-rendered job cards and a tiny SSE client |
| Storage | SQLite via `aiosqlite` | Zero external services; easy local development |
| Bot | `python-telegram-bot` v21 | Async polling, conversations, inline handoff buttons |
| Scope | Watch + alert; opt-in seat hold with manual payment | Payments stay with you; no evasion or payment automation |

## Quick start

```bash
cp .env.example .env
uv sync
uv run python -m bms_assistant
```

Open <http://127.0.0.1:8765>.

The app runs fine **without Telegram**. Set a token later to enable push alerts.

Local development has no login by default because the app binds to `127.0.0.1`. Setting `BMS_AUTH_PASSWORD` enables the login page; `BMS_ENVIRONMENT=production` requires it. See [Authentication](#authentication) and [Deploying](#deploying).

### First dev test

1. Click **New watch**.
2. Open **Advanced / testing** and choose **Mock drop (dev)**.
3. Click **set +15s (dev)** next to the release time.
4. Enter a name and venue, keep 2 tickets, and create the watch.
5. Watch the card transition to **Live now**; the dashboard beeps and the activity feed updates.
6. Click **Open booking** (a demo link) or **Mark booked**.

You can also use the mock provider from Telegram with `/newjob` → Mock → `/now` at the release step.

### Create a real BMS watch

The normal provider is **Scheduled reminder** — a local scheduled alert with
your BMS link, no scraping and no login required in the app.

1. Open the movie or seat page on BookMyShow in your browser.
2. Copy the URL from the address bar.
3. In the dashboard, click **New watch** and paste it into **Paste your BookMyShow link**.
   The movie, event code, city, venue code, and show date are filled in from the URL.
4. If the movie page has no showtimes yet, pick **Time window** and enter the
   earliest and latest times you'd accept (for example, 18:00–23:30). If you don't
   know when tickets open, choose **Start watching now — drop time unknown**.
5. Set your seat category and maximum price if you want them recorded for the
   auto-cart step. Payment is still manual.
6. Leave **Start watching immediately** checked and create the watch.

The web guide at <http://127.0.0.1:8765/guide> and the Telegram `/guide` command
have annotated screenshots showing exactly which part of the BMS URL is used.
Seat-page links do not contain the movie title, so type the movie name if that
field stays blank.

Showtime modes:

- **Any showtime on that date** — alert for any show on the selected date.
- **Time window** — best for unreleased schedules; accepts a range such as `18:00–23:30`.
- **Exact showtime** — only when BMS has published the time you want.

For scripted dev tests:

```bash
curl -X POST http://127.0.0.1:8765/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"name":"API test","provider":"mock","venue":"Test","release_at":"2030-01-01T20:00:00+05:30","quantity":2,"interval_seconds":2}'
```

## Telegram setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Put it in `.env`:
   ```dotenv
   BMS_TELEGRAM_BOT_TOKEN=123456789:...
   ```
3. Message your bot `/start`. It replies with your chat id.
4. Set that chat id:
   ```dotenv
   BMS_TELEGRAM_CHAT_ID=123456789
   ```
5. Restart the app.

`BMS_TELEGRAM_CHAT_ID` does two jobs: it is the default alert destination for web-created watches, and it **locks the bot to that chat**. Other users can still run `/start` to see their own chat id, but every other command and button is ignored. If the variable is unset, the bot logs a warning and accepts commands from anyone. Production requires it whenever a token is configured.

Watches created from Telegram store that chat id automatically. Web-created watches use `BMS_TELEGRAM_CHAT_ID`.

Useful commands: `/newjob`, `/guide`, `/jobs`, `/job <id>`, `/run`, `/stop`, `/booked`, `/delete`, `/testalert`, `/status`.

## Run it on your own machine (free)

The most practical free setup: keep the watcher running on this machine and use Telegram as the remote control. No hosting, no card, and SQLite stays on disk between restarts.

```bash
./scripts/run-local.sh
```

The script wraps the app in `caffeinate` so macOS does not idle-sleep while it runs. Things to know:

- Keep the Mac plugged in, and keep the lid **open** on a MacBook — closing the lid sleeps it regardless.
- Run it inside `tmux` (or a launchd agent) if you want it to survive closing the terminal.
- Telegram uses long polling, so no open ports, router changes, or public IP are needed.
- If the machine is offline at drop time, no alert can reach you. That is the trade-off for free.

### Reach the dashboard from your phone (optional)

The app binds to `127.0.0.1`, so the dashboard is only reachable on this machine. A free Cloudflare quick tunnel exposes it over HTTPS with no account:

```bash
brew install cloudflared
cloudflared tunnel --url http://127.0.0.1:8765
```

Open the printed `https://…trycloudflare.com` URL on your phone. **Set `BMS_AUTH_PASSWORD` first** — that URL is public, and without a password anyone who finds it can control your watches:

```dotenv
BMS_AUTH_PASSWORD=a-long-random-password
```

The URL changes every time the tunnel restarts. Tailscale Funnel (`tailscale funnel 8765`) is the alternative if you want a stable `*.ts.net` address.

## BMS auto-cart (opt-in)

> **Read this first.** This module automates a real checkout flow on BookMyShow
> with your own account. That violates BookMyShow's terms of service and can get
> your account suspended. It is **off by default**, it **never pays** — the flow
> stops at the payment-options screen and you complete every payment by hand —
> and it has no stealth or evasion features. Use it only if you accept those risks.

The module is landing in stages:

| Stage | Status |
| --- | --- |
| Recon tooling (capture what BMS actually sends) | available now |
| Seat scoring (`services/seat_preferences.py`) | available now |
| Availability watcher + auto-cart + hold handoff | in progress, gated behind `BMS_AUTOCART_ENABLED` (default `false`) when it lands |

### Setup

```bash
uv sync --extra bms
uv run playwright install chromium
```

### Step 1: log in once, by hand

```bash
uv run python scripts/bms-login.py
```

A Chromium window opens with a dedicated profile in `data/browser-profile/`. Log in to BMS there (finish any OTP), then press Enter. The profile — including its session cookies — stays on this machine, is gitignored, and is blocked by the secret guard. The scripts never see your password.

### Step 2: capture a recon session

```bash
uv run python scripts/bms-recon.py --url "https://in.bookmyshow.com/explore/home/<city>"
```

The browser opens on that page. Walk to a real show's seat-selection page, then to the payment-options screen (do not pay), and watch the terminal:

- every JSON XHR/fetch response is saved under `data/bms-recon/<stamp>/json/`,
- a screenshot is saved on each navigation,
- console messages and page errors go to `console.log`, bookmyshow POST bodies (redacted) to `requests.log`, and browser automation flags to `env.json`,
- type `s` for an extra screenshot, `d` for a DOM snapshot, `q` to quit and write `index.json`.

Headers and cookies are never captured, and obvious secret/PII keys (tokens, emails, phones, UPI ids) are replaced with `[REDACTED]` — best effort, so review anything before sharing it.

### Ground rules that will not change

- Payment is always manual: no UPI, card, or gift-voucher automation of any kind.
- No stealth, fingerprint spoofing, CAPTCHA solving, or proxy rotation.
- Login is manual and one-time; credentials and PINs are never stored.
- One hold at a time, one account, polite polling with backoff.

### Why there is no saved card or UPI ID

Saving payment credentials would make the app a more attractive target without
making checkout meaningfully faster:

- A stored card needs the number, expiry, CVV, and often an OTP. Keeping those on
  disk turns a ticket-watch database into a payment-credential database.
- A UPI ID by itself cannot authorize a payment. The charge is approved with a
  PIN or biometric inside your UPI app, and that approval must stay with you.
- A half-automated checkout that stores or replays payment data would cross the
  same line as automated payment, which this project explicitly does not do.

Instead, the handoff gives you the show details, the BMS link, the ticket count,
and a hold countdown. You choose seats and pay in BMS (or in your UPI/bank app)
with the credentials already saved by those apps.

## Configuration

All settings use the `BMS_` prefix and are loaded from `.env`.

| Variable | Default | Notes |
| --- | --- | --- |
| `BMS_ENVIRONMENT` | `dev` | `production` enables public-deployment safety checks |
| `BMS_HOST` | `127.0.0.1` | Use `0.0.0.0` only inside Docker or a trusted network |
| `BMS_PORT` | `8765` | Web UI port. The platform `PORT` is honored when `BMS_PORT` is unset |
| `BMS_TIMEZONE` | `Asia/Kolkata` | All form and Telegram times are parsed in this zone |
| `BMS_DATABASE_PATH` | `data/bms_assistant.db` | Relative paths resolve to the project root |
| `BMS_DEFAULT_CHECK_INTERVAL_SECONDS` | `5` | Default poll interval |
| `BMS_MIN_CHECK_INTERVAL_SECONDS` | `2` | Hard floor for intervals |
| `BMS_MAX_CONSECUTIVE_ERRORS` | `5` | Provider errors before a watch is failed |
| `BMS_TELEGRAM_ENABLED` | `true` | Set `false` to disable the bot entirely |
| `BMS_TELEGRAM_BOT_TOKEN` | unset | Enables the bot when present |
| `BMS_TELEGRAM_CHAT_ID` | unset | Alert destination and the only chat allowed to control the bot |
| `BMS_AUTH_PASSWORD` | unset | Enables dashboard login. Required in production, minimum 12 characters |
| `BMS_SESSION_SECRET` | unset | Signs the login cookie. Set it in production to survive restarts |
| `BMS_API_TOKEN` | unset | Optional bearer token for scripted `/api/*` calls |
| `BMS_SECURE_COOKIES` | auto | Defaults to `true` in production; set explicitly if TLS is terminated elsewhere |

## Authentication

Local development is intentionally frictionless: with no `BMS_AUTH_PASSWORD`, the dashboard has no login and should stay on `127.0.0.1`.

For anything reachable from the internet:

```dotenv
BMS_ENVIRONMENT=production
BMS_AUTH_PASSWORD=a-long-random-password
BMS_SESSION_SECRET=a-separate-random-secret
BMS_API_TOKEN=optional-token-for-scripts
```

- `BMS_ENVIRONMENT=production` refuses to start without a password.
- The login cookie is HTTP-only, `SameSite=Lax`, and secure in production.
- `BMS_SESSION_SECRET` signs the cookie. Without it, a random secret is generated at startup, so every restart signs you out.
- With auth enabled, `/api/*` accepts either the browser cookie or `Authorization: Bearer $BMS_API_TOKEN`.
- `/health` and `/static/*` remain public. The health endpoint only exposes counts.
- Failed logins are delayed and logged.

Generate secrets locally with `openssl rand -hex 32`. Never commit them; the pre-commit secret scanner blocks `.env` and key material.

## Architecture

```text
src/bms_assistant/
├── config.py                 # pydantic-settings, timezone, Telegram config
├── main.py                   # composition root + FastAPI lifespan
├── domain/                   # Job, Availability, events, errors
├── ports/                    # provider, repository, notifier protocols
├── services/
│   ├── job_manager.py        # monitoring loop + state transitions
│   ├── event_bus.py          # in-process SSE fan-out
│   └── seat_preferences.py   # pure seat scoring for the opt-in auto-cart flow
├── adapters/
│   ├── providers/            # mock + scheduled-reminder providers
│   ├── storage/sqlite.py     # repository adapter
│   └── notify/               # console + Telegram notifiers
├── web/                      # FastAPI routes, Jinja templates, CSS/JS
└── bot/                      # Telegram handlers and lifecycle
```

The dependency rule is inward: `domain` knows nothing about FastAPI, SQLite, or Telegram. `services` depend on ports. Adapters implement ports.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Dashboard |
| `GET` | `/guide` | Illustrated BMS link and setup guide |
| `GET` | `/health` | Health and watch counts |
| `GET` | `/api/jobs` | All watches as JSON |
| `GET` | `/api/jobs/{id}` | One watch |
| `GET` | `/api/providers` | Provider metadata |
| `GET` | `/api/events` | SSE stream of job events |
| `POST` | `/api/jobs` | Create a watch (JSON; good for scripts) |
| `POST` | `/jobs` | Create a watch (form encoded) |
| `POST` | `/jobs/{id}/start` | Start watching |
| `POST` | `/jobs/{id}/stop` | Pause |
| `POST` | `/jobs/{id}/check` | One manual provider check |
| `POST` | `/jobs/{id}/booked` | Mark as booked |
| `POST` | `/jobs/{id}/delete` | Delete |

When authentication is enabled, send the browser cookie or `Authorization: Bearer $BMS_API_TOKEN`. `/health` stays public for platform health checks.

`POST /api/jobs` also accepts the BMS criteria fields: `movie_id`, `venue_id`,
`city`, `show_date` (`YYYY-MM-DD`), `window_start` and `window_end` (`HH:MM`), and
`seat_prefs` (`{"category": "GOLD", "max_price": 350, "zone": "center", "need_adjacent": true}`).
The form endpoint `POST /jobs` additionally accepts `bms_url`, `showtime_mode`
(`any`, `window`, `exact`), `release_mode` (`at`, `now`), and the seat preference
form fields, and parses the BMS URL into those criteria.

## Adding a provider

Implement the `TicketProvider` protocol in `src/bms_assistant/adapters/providers/`:

```python
class MyProvider:
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="my-provider",
            label="My provider",
            description="Reads availability only.",
        )

    async def check(self, job: Job) -> Availability:
        # Observe availability. Never purchase, pay, or bypass access controls.
        ...
```

Register it in `build_default_registry()`. The job manager, web UI, and Telegram bot pick it up automatically. See [`docs/providers.md`](docs/providers.md) for the contract and testing tips.

Providers must be side-effect free. If you integrate with a platform, use an official or permitted interface, respect its terms, and keep the handoff manual.

## Deploying

This app is designed for **one always-on instance with a persistent disk**. The monitor loop, Telegram long polling, in-process SSE bus, and SQLite all assume a single process. Do not enable autoscaling, and do not run multiple replicas against the same database.

If you would rather not pay for hosting, skip this section and [run it on your own machine](#run-it-on-your-own-machine-free) instead.

`BMS_ENVIRONMENT=production` enforces the public-deployment basics:

- `BMS_AUTH_PASSWORD` must be set (12+ characters).
- `BMS_TELEGRAM_CHAT_ID` must be set when a Telegram bot token is configured.

Fly.io and Render configs are included. Both mount a volume at `/data` and keep SQLite there.

### Fly.io

[`fly.toml`](fly.toml) defines one machine, no auto-stop, a `/health` check, and a `bms_data` volume at `/data`.

```bash
fly launch --no-deploy
fly volumes create bms_data --size 1 --region bom
fly secrets set \
  BMS_AUTH_PASSWORD='a-long-random-password' \
  BMS_SESSION_SECRET="$(openssl rand -hex 32)" \
  BMS_TELEGRAM_BOT_TOKEN='123456789:...' \
  BMS_TELEGRAM_CHAT_ID='123456789'
fly deploy
fly logs
```

Change `app` and `primary_region` in `fly.toml` if needed; the volume must be in the same region as the machine. Leave the app at one machine (`fly scale count 1`). The dashboard is at `https://<app>.fly.dev`.

### Render

[`render.yaml`](render.yaml) defines a Docker web service with a 1 GB disk at `/data`. Persistent disks require a paid instance type (Starter or higher).

1. In Render, choose **New → Blueprint** and select this repository.
2. Set `BMS_AUTH_PASSWORD`, `BMS_TELEGRAM_BOT_TOKEN`, and `BMS_TELEGRAM_CHAT_ID` when prompted (`BMS_SESSION_SECRET` is generated).
3. Apply the blueprint and keep the service at one instance.

If you do not use Telegram, leave the token blank; the bot stays disabled.

### Other hosts

Any single always-on container with a volume works (Railway, a VPS, your own Docker host). Mount the volume at `/data`, set `BMS_DATABASE_PATH=/data/bms_assistant.db`, set `BMS_HOST=0.0.0.0`, and set the auth environment variables. If the platform injects `PORT`, the app honors it unless `BMS_PORT` is also set.

### Why not FastAPI Cloud?

FastAPI Cloud is request-driven and scales to zero on the free plan, and it does not provide a durable local disk. This app needs a process that keeps polling when no one is visiting the dashboard, exactly one replica, and persistent SQLite. Deploying there would require a Postgres adapter, a split web/worker architecture, leader election, and a shared event bus — a different design, not a config change.

## Docker

```bash
cp .env.example .env
docker compose up -d --build
docker compose logs -f
```

The compose file binds the UI to `127.0.0.1` on the host and persists SQLite data in `./data`. Set `BMS_HOST=0.0.0.0` only inside the container (the compose file does this). Set `BMS_AUTH_PASSWORD` before exposing the dashboard beyond localhost.

## Development

```bash
uv run ruff check .
uv run ruff format .
uv run pytest
uv run pre-commit install
```

`uv run uvicorn bms_assistant.main:create_app --factory --reload` gives hot reload. Use the normal `uv run python -m bms_assistant` entrypoint for the Telegram bot.

Working on the opt-in BMS module? `uv sync --extra bms` installs Playwright and `uv run playwright install chromium` downloads the browser. Only the scripts under `scripts/` and the future auto-cart adapter need it; the base install and Docker image stay slim.

The pre-commit suite includes Gitleaks, `detect-private-key`, and a file guard that blocks `.env`, local `data/`, databases, and key material. Gitleaks also runs over the full git history in CI, and GitHub secret scanning plus push protection are enabled for the repository. Install the hooks once with `uv run pre-commit install`.

The Gitleaks hook is built by pre-commit and needs a Go toolchain on the first run. CI downloads a pinned Gitleaks release, so it does not need Go.

If a secret is ever committed, rotate it immediately. Removing it in a later commit does not remove it from git history or from forks and caches. See [`SECURITY.md`](SECURITY.md).

## Scope and responsible use

This project alerts you, and — with the opt-in auto-cart module — can drive a browser to select and hold seats. It does not bypass bot protections, solve CAPTCHAs, automate logins (the browser login is manual and one-time), store payment credentials, or complete payments.

Two hard rules:

- **Payments stay manual.** UPI and card payments require your approval in your own app by design; nothing here will ever try to script that, and any tool claiming otherwise should not be trusted with your money.
- **You carry the risk.** The auto-cart module operates against BookMyShow's terms of service and can get your account suspended. Use the project only with services and workflows you are allowed to automate.
