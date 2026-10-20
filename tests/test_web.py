from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from httpx import ASGITransport, AsyncClient

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
