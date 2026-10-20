from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from bms_assistant.adapters.providers.registry import build_default_registry
from bms_assistant.adapters.storage.sqlite import SQLiteJobRepository
from bms_assistant.config import Settings
from bms_assistant.services.event_bus import EventBus
from bms_assistant.services.job_manager import JobManager
from bms_assistant.web.app import create_web_app


async def test_dashboard_and_create_job(tmp_path):
    settings = Settings(
        database_path=tmp_path / "web.db",
        telegram_enabled=False,
        timezone="UTC",
        default_check_interval_seconds=2,
        min_check_interval_seconds=2,
        _env_file=None,
    )
    repository = SQLiteJobRepository(settings.database_path)
    await repository.init()
    providers = build_default_registry()
    manager = JobManager(
        repository=repository,
        providers=providers,
        settings=settings,
        event_bus=EventBus(),
    )
    app = create_web_app(
        settings=settings,
        job_manager=manager,
        repository=repository,
        providers=providers,
        event_bus=EventBus(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert "Watchlist" in response.text
        assert 'id="open-job-dialog"' in response.text
        assert 'value="reminder"' in response.text
        assert "Scheduled reminder" in response.text

        release = (datetime.now(UTC) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M")
        payload = {
            "name": "Web test",
            "provider": "mock",
            "venue": "Test venue",
            "release_at": release,
            "quantity": "2",
            "interval_seconds": "2",
            "auto_start": "on",
        }
        created = await client.post(
            "/jobs",
            content=urlencode(payload),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert created.status_code == 303

        jobs = await client.get("/api/jobs")
        assert jobs.status_code == 200
        body = jobs.json()
        assert len(body["jobs"]) == 1
        assert body["jobs"][0]["name"] == "Web test"
        assert body["jobs"][0]["status"] == "monitoring"

        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["jobs"] == 1

        api_created = await client.post(
            "/api/jobs",
            json={
                "name": "API test",
                "provider": "reminder",
                "venue": "API venue",
                "release_at": "2030-01-01T20:00:00+05:30",
                "booking_url": "https://example.org/book",
                "quantity": 1,
                "auto_start": False,
            },
        )
        assert api_created.status_code == 201
        assert api_created.json()["status"] == "scheduled"

    await manager.shutdown()


async def test_bms_link_form_fills_codes_and_window(tmp_path):
    settings = Settings(
        database_path=tmp_path / "bms-web.db",
        telegram_enabled=False,
        timezone="UTC",
        default_check_interval_seconds=2,
        min_check_interval_seconds=2,
        _env_file=None,
    )
    repository = SQLiteJobRepository(settings.database_path)
    await repository.init()
    providers = build_default_registry()
    manager = JobManager(
        repository=repository,
        providers=providers,
        settings=settings,
        event_bus=EventBus(),
    )
    app = create_web_app(
        settings=settings,
        job_manager=manager,
        repository=repository,
        providers=providers,
        event_bus=EventBus(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        guide = await client.get("/guide")
        assert guide.status_code == 200
        assert "How to add a BMS watch" in guide.text
        assert "ET00521930" in guide.text
        assert 'href="/?new=1"' in guide.text
        assert 'id="open-job-dialog"' not in guide.text

        payload = {
            "bms_url": (
                "https://in.bookmyshow.com/movies/hyderabad/seat-layout/"
                "ET00521930/ALUC/6296/20261022"
            ),
            "name": "Jailer 2",
            "provider": "reminder",
            "release_mode": "now",
            "showtime_mode": "window",
            "window_start": "18:00",
            "window_end": "23:30",
            "quantity": "2",
            "seat_category": "GOLD",
            "seat_max_price": "350",
            "seat_zone": "center",
            "seats_together": "on",
            "interval_seconds": "2",
        }
        created = await client.post(
            "/jobs",
            content=urlencode(payload),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert created.status_code == 303

        jobs = await client.get("/api/jobs")
        assert jobs.status_code == 200
        job = jobs.json()["jobs"][0]
        assert job["name"] == "Jailer 2"
        assert job["provider"] == "reminder"
        assert job["movie_id"] == "ET00521930"
        assert job["venue_id"] == "ALUC"
        assert job["city"] == "hyderabad"
        assert job["show_date"] == "2026-10-22"
        assert job["window_start"] == "18:00:00"
        assert job["window_end"] == "23:30:00"
        assert job["booking_url"].endswith("/ALUC/6296/20261022")
        assert job["availability_verified"] is False
        assert job["seat_prefs"] == {
            "category": "GOLD",
            "max_price": 350,
            "zone": "center",
            "need_adjacent": True,
        }
        assert job["status"] == "scheduled"

    await manager.shutdown()


async def test_reminder_card_does_not_claim_live_availability(tmp_path):
    settings = Settings(
        database_path=tmp_path / "reminder-card.db",
        telegram_enabled=False,
        timezone="UTC",
        default_check_interval_seconds=2,
        min_check_interval_seconds=2,
        _env_file=None,
    )
    repository = SQLiteJobRepository(settings.database_path)
    await repository.init()
    providers = build_default_registry()
    manager = JobManager(
        repository=repository,
        providers=providers,
        settings=settings,
        event_bus=EventBus(),
    )
    app = create_web_app(
        settings=settings,
        job_manager=manager,
        repository=repository,
        providers=providers,
        event_bus=EventBus(),
    )
    job = await manager.create_job(
        name="Ramayana",
        provider="reminder",
        venue="ALLU Cinemas",
        quantity=2,
        release_at=datetime.now(UTC) - timedelta(seconds=1),
        booking_url="https://in.bookmyshow.com/movies/hyderabad/ramayana/ET00451914",
        auto_start=True,
    )

    for _ in range(50):
        current = await manager.get_job(job.id)
        if current.status.value == "triggered":
            break
        await asyncio.sleep(0.05)
    assert (await manager.get_job(job.id)).status.value == "triggered"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        dashboard = await client.get("/")
        assert dashboard.status_code == 200
        assert "Reminder due" in dashboard.text
        assert "not a live BMS check" in dashboard.text
        assert "Check BMS showtimes" in dashboard.text
        assert "Tickets look live" not in dashboard.text

    await manager.shutdown()


async def _auth_test_app(tmp_path, **overrides):
    settings = Settings(
        database_path=tmp_path / "auth.db",
        telegram_enabled=False,
        timezone="UTC",
        auth_password="correct-horse-battery",
        session_secret="test-session-secret",
        _env_file=None,
        **overrides,
    )
    repository = SQLiteJobRepository(settings.database_path)
    await repository.init()
    providers = build_default_registry()
    manager = JobManager(
        repository=repository,
        providers=providers,
        settings=settings,
        event_bus=EventBus(),
    )
    app = create_web_app(
        settings=settings,
        job_manager=manager,
        repository=repository,
        providers=providers,
        event_bus=EventBus(),
    )
    return settings, manager, app


async def test_web_auth_requires_login(tmp_path):
    settings, manager, app = await _auth_test_app(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        dashboard = await client.get("/")
        assert dashboard.status_code == 303
        assert dashboard.headers["location"].startswith("/login")

        api = await client.get("/api/jobs")
        assert api.status_code == 401

        health = await client.get("/health")
        assert health.status_code == 200

        wrong = await client.post(
            "/login",
            data={"password": "wrong-password", "next": "/"},
        )
        assert wrong.status_code == 401
        assert "Wrong password" in wrong.text
        assert "bms_session" not in client.cookies

        login = await client.post(
            "/login",
            data={"password": settings.auth_password, "next": "/"},
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/"
        assert client.cookies.get("bms_session")

        dashboard = await client.get("/")
        assert dashboard.status_code == 200
        assert "Watchlist" in dashboard.text

        jobs = await client.get("/api/jobs")
        assert jobs.status_code == 200

        partial = await client.get("/partials/dashboard")
        assert partial.status_code == 200

        await client.post("/logout")
        assert not client.cookies.get("bms_session")
        assert (await client.get("/api/jobs")).status_code == 401

    await manager.shutdown()


async def test_login_ignores_offsite_next(tmp_path):
    _, manager, app = await _auth_test_app(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/login",
            data={"password": "correct-horse-battery", "next": "//evil.example/steal"},
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/"

    await manager.shutdown()


async def test_api_bearer_token(tmp_path):
    settings, manager, app = await _auth_test_app(tmp_path, api_token="script-token")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "name": "Bearer test",
            "provider": "reminder",
            "venue": "API venue",
            "release_at": "2030-01-01T20:00:00+05:30",
            "booking_url": "https://example.org/book",
            "quantity": 1,
            "auto_start": False,
        }
        denied = await client.post("/api/jobs", json=payload)
        assert denied.status_code == 401

        allowed = await client.post(
            "/api/jobs",
            json=payload,
            headers={"Authorization": f"Bearer {settings.api_token}"},
        )
        assert allowed.status_code == 201
        assert allowed.json()["name"] == "Bearer test"

    await manager.shutdown()


def test_production_requires_auth_password():
    with pytest.raises(ValidationError, match="BMS_AUTH_PASSWORD"):
        Settings(environment="production", telegram_enabled=False, _env_file=None)


def test_production_requires_telegram_chat_id():
    with pytest.raises(ValidationError, match="BMS_TELEGRAM_CHAT_ID"):
        Settings(
            environment="production",
            auth_password="correct-horse-battery",
            telegram_bot_token="123456789:fake",
            _env_file=None,
        )


def test_short_auth_password_rejected():
    with pytest.raises(ValidationError, match="at least 12"):
        Settings(auth_password="short", _env_file=None)
