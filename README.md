# Ticket Watch

A local-first, semi-automated ticket **watchlist** with a web dashboard and a Telegram bot. It checks a release window, alerts you loudly when tickets look live, and hands you the booking page. You choose the seats and approve the payment.

It is deliberately **not** a checkout bot. There is no anti-bot evasion, CAPTCHA solving, stored card/UPI data, or automated OTP handling.

## What it does

- Create watches from the web UI or Telegram.
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
| Scope | Watch + alert + manual checkout | Keeps payments and platform rules with the user |

## Quick start

```bash
cp .env.example .env
uv sync
uv run python -m bms_assistant
```

Open <http://127.0.0.1:8765>.

The app runs fine **without Telegram**. Set a token later to enable push alerts.

### First dev test

1. Click **New watch**.
2. Leave the provider as **Mock drop (dev)**.
3. Click **set +15s (dev)** next to the release time.
4. Enter a name and venue, keep 2 tickets, and create the watch.
5. Watch the card transition to **Live now**; the dashboard beeps and the activity feed updates.
6. Click **Open booking** (a demo link) or **Mark booked**.

You can also use the mock provider from Telegram with `/newjob` → Mock → `/now` at the release step.

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
4. For alerts about watches created in the **web UI**, also set:
   ```dotenv
   BMS_TELEGRAM_CHAT_ID=123456789
   ```
5. Restart the app.

Watches created from Telegram store that chat id automatically. Web-created watches use `BMS_TELEGRAM_CHAT_ID`.

Useful commands: `/newjob`, `/jobs`, `/job <id>`, `/run`, `/stop`, `/booked`, `/delete`, `/testalert`, `/status`.

## Configuration

All settings use the `BMS_` prefix and are loaded from `.env`.

| Variable | Default | Notes |
| --- | --- | --- |
| `BMS_HOST` | `127.0.0.1` | Use `0.0.0.0` only inside Docker or a trusted network |
| `BMS_PORT` | `8765` | Web UI port |
| `BMS_TIMEZONE` | `Asia/Kolkata` | All form and Telegram times are parsed in this zone |
| `BMS_DATABASE_PATH` | `data/bms_assistant.db` | Relative paths resolve to the project root |
| `BMS_DEFAULT_CHECK_INTERVAL_SECONDS` | `5` | Default poll interval |
| `BMS_MIN_CHECK_INTERVAL_SECONDS` | `2` | Hard floor for intervals |
| `BMS_MAX_CONSECUTIVE_ERRORS` | `5` | Provider errors before a watch is failed |
| `BMS_TELEGRAM_ENABLED` | `true` | Set `false` to disable the bot entirely |
| `BMS_TELEGRAM_BOT_TOKEN` | unset | Enables the bot when present |
| `BMS_TELEGRAM_CHAT_ID` | unset | Default chat for web-created watches |

## Architecture

```text
src/bms_assistant/
├── config.py                 # pydantic-settings, timezone, Telegram config
├── main.py                   # composition root + FastAPI lifespan
├── domain/                   # Job, Availability, events, errors
├── ports/                    # provider, repository, notifier protocols
├── services/
│   ├── job_manager.py        # monitoring loop + state transitions
│   └── event_bus.py          # in-process SSE fan-out
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

## Docker

```bash
cp .env.example .env
docker compose up -d --build
docker compose logs -f
```

The compose file binds the UI to `127.0.0.1` on the host and persists SQLite data in `./data`. Set `BMS_HOST=0.0.0.0` only inside the container (the compose file does this). Do not expose the dashboard publicly without adding authentication.

## Development

```bash
uv run ruff check .
uv run ruff format .
uv run pytest
uv run pre-commit install
```

`uv run uvicorn bms_assistant.main:create_app --factory --reload` gives hot reload. Use the normal `uv run python -m bms_assistant` entrypoint for the Telegram bot.

The pre-commit suite includes Gitleaks, `detect-private-key`, and a file guard that blocks `.env`, local `data/`, databases, and key material. Gitleaks also runs over the full git history in CI, and GitHub secret scanning plus push protection are enabled for the repository. Install the hooks once with `uv run pre-commit install`.

The Gitleaks hook is built by pre-commit and needs a Go toolchain on the first run. CI downloads a pinned Gitleaks release, so it does not need Go.

If a secret is ever committed, rotate it immediately. Removing it in a later commit does not remove it from git history or from forks and caches. See [`SECURITY.md`](SECURITY.md).

## Scope and responsible use

This project is an alerting and handoff assistant. It does not bypass bot protections, solve CAPTCHAs, automate logins, or complete payments. Use it only with services and workflows you are allowed to automate, and follow the terms of the platforms you use. Payments always require your explicit action.
