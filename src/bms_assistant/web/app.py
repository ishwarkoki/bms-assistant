"""FastAPI web dashboard and small JSON/SSE API."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from bms_assistant.config import Settings
from bms_assistant.domain.errors import JobNotFoundError
from bms_assistant.domain.models import STATUS_LABELS, utcnow
from bms_assistant.ports.providers import ProviderRegistry
from bms_assistant.ports.repository import JobRepository
from bms_assistant.services.event_bus import EventBus
from bms_assistant.services.job_manager import JobManager
from bms_assistant.timeutils import format_local, format_local_input, format_relative
from bms_assistant.web.forms import parse_new_job_form

WEB_ROOT = Path(__file__).resolve().parent


class JobCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider: str = "mock"
    venue: str = Field(min_length=1, max_length=160)
    release_at: datetime
    show_at: datetime | None = None
    quantity: int = Field(2, ge=1, le=10)
    booking_url: str | None = None
    interval_seconds: int | None = Field(None, ge=1)
    auto_start: bool = True


def _as_utc(value: datetime, tz: Any) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    return value.astimezone(UTC)


def _redirect(path: str, **params: str) -> RedirectResponse:
    query = urlencode({key: value for key, value in params.items() if value})
    url = f"{path}?{query}" if query else path
    return RedirectResponse(url=url, status_code=303)


def create_web_app(
    *,
    settings: Settings,
    job_manager: JobManager,
    repository: JobRepository,
    providers: ProviderRegistry,
    event_bus: EventBus,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[Any]] | None = None,
) -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=WEB_ROOT / "templates")

    templates.env.filters["localtime"] = lambda value: format_local(value, settings.tz)
    templates.env.filters["localdate"] = lambda value: format_local(
        value, settings.tz, "%a, %d %b %Y"
    )
    templates.env.filters["localinput"] = lambda value: format_local_input(value, settings.tz)
    templates.env.filters["relative"] = format_relative
    templates.env.globals.update(
        app_name=settings.app_name,
        timezone=settings.timezone,
        telegram_ready=settings.telegram_ready,
        status_labels=STATUS_LABELS,
    )

    async def dashboard_context(
        request: Request,
        *,
        notice: str | None = None,
        error: str | None = None,
        form: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        jobs = await job_manager.list_jobs()
        events = await job_manager.list_events(60)
        stats = await job_manager.stats()
        default_release = format_local_input(utcnow() + timedelta(minutes=2), settings.tz)
        return {
            "request": request,
            "jobs": jobs,
            "events": events,
            "stats": stats,
            "providers": providers.list(),
            "notice": notice,
            "error": error,
            "form": form or {},
            "form_error": error if form else None,
            "default_release_at": default_release,
            "default_interval": settings.effective_check_interval,
            "min_interval": settings.min_check_interval_seconds,
        }

    async def render_dashboard(
        request: Request,
        *,
        notice: str | None = None,
        error: str | None = None,
        form: dict[str, Any] | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        context = await dashboard_context(
            request,
            notice=notice,
            error=error,
            form=form,
        )
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=context,
            status_code=status_code,
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        if request.url.path == "/":
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        return await render_dashboard(
            request,
            notice=request.query_params.get("notice"),
            error=request.query_params.get("error"),
        )

    @app.get("/partials/dashboard", response_class=HTMLResponse)
    async def dashboard_partial(request: Request) -> HTMLResponse:
        context = await dashboard_context(request)
        return templates.TemplateResponse(
            request=request,
            name="partials/dashboard.html",
            context=context,
        )

    @app.get("/partials/activity", response_class=HTMLResponse)
    async def activity_partial(request: Request) -> HTMLResponse:
        events = await job_manager.list_events(60)
        return templates.TemplateResponse(
            request=request,
            name="partials/activity.html",
            context={"request": request, "events": events},
        )

    @app.post("/jobs")
    async def create_job(request: Request) -> Response:
        form = await request.form()
        data = {key: str(value) for key, value in form.items()}
        try:
            parsed = parse_new_job_form(
                data,
                tz=settings.tz,
                default_interval=settings.effective_check_interval,
                min_interval=settings.min_check_interval_seconds,
            )
            job = await job_manager.create_job(
                name=parsed.name,
                provider=parsed.provider,
                venue=parsed.venue,
                quantity=parsed.quantity,
                release_at=parsed.release_at,
                show_at=parsed.show_at,
                booking_url=parsed.booking_url,
                interval_seconds=parsed.interval_seconds,
                auto_start=parsed.auto_start,
                chat_id=settings.telegram_chat_id,
            )
        except (ValueError, JobNotFoundError) as exc:
            return await render_dashboard(request, error=str(exc), form=data, status_code=400)

        state = "watching" if job.is_watching else "scheduled"
        return _redirect("/", notice=f"Watch {job.short_id} created and {state}.")

    async def simple_action(job_id: str, action: str) -> Response:
        try:
            job = await job_manager.get_job(job_id)
            if action == "start":
                job = await job_manager.start(job.id)
                notice = f"Watching {job.short_id} — {job.name}."
            elif action == "stop":
                job = await job_manager.stop(job.id)
                notice = f"Paused {job.short_id} — {job.name}."
            elif action == "booked":
                job = await job_manager.mark_booked(job.id)
                notice = f"Marked {job.short_id} as booked."
            elif action == "delete":
                job = await job_manager.delete(job.id)
                notice = f"Deleted {job.short_id} — {job.name}."
            elif action == "check":
                result = await job_manager.check_now(job.id)
                notice = f"Check {job.short_id}: {result.detail or 'completed'}."
            else:
                notice = "Unknown action."
        except JobNotFoundError as exc:
            return _redirect("/", error=str(exc))
        except ValueError as exc:
            return _redirect("/", error=str(exc))
        except Exception as exc:
            return _redirect("/", error=f"Action failed: {exc}")
        return _redirect("/", notice=notice)

    @app.post("/jobs/{job_id}/start")
    async def start_job(job_id: str) -> Response:
        return await simple_action(job_id, "start")

    @app.post("/jobs/{job_id}/stop")
    async def stop_job(job_id: str) -> Response:
        return await simple_action(job_id, "stop")

    @app.post("/jobs/{job_id}/booked")
    async def booked_job(job_id: str) -> Response:
        return await simple_action(job_id, "booked")

    @app.post("/jobs/{job_id}/delete")
    async def delete_job(job_id: str) -> Response:
        return await simple_action(job_id, "delete")

    @app.post("/jobs/{job_id}/check")
    async def check_job(job_id: str) -> Response:
        return await simple_action(job_id, "check")

    @app.post("/api/jobs", status_code=201)
    async def api_create_job(payload: JobCreateRequest) -> JSONResponse:
        try:
            job = await job_manager.create_job(
                name=payload.name,
                provider=payload.provider,
                venue=payload.venue,
                quantity=payload.quantity,
                release_at=_as_utc(payload.release_at, settings.tz),
                show_at=_as_utc(payload.show_at, settings.tz) if payload.show_at else None,
                booking_url=payload.booking_url,
                interval_seconds=payload.interval_seconds,
                auto_start=payload.auto_start,
                chat_id=settings.telegram_chat_id,
            )
        except (ValueError, JobNotFoundError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(job.to_public_dict(), status_code=201)

    @app.get("/api/jobs")
    async def api_jobs() -> JSONResponse:
        jobs = await job_manager.list_jobs()
        return JSONResponse({"jobs": [job.to_public_dict() for job in jobs]})

    @app.get("/api/jobs/{job_id}")
    async def api_job(job_id: str) -> JSONResponse:
        try:
            job = await job_manager.get_job(job_id)
        except JobNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(job.to_public_dict())

    @app.get("/api/providers")
    async def api_providers() -> JSONResponse:
        return JSONResponse({"providers": [asdict(info) for info in providers.list()]})

    @app.get("/api/events")
    async def api_events(request: Request) -> StreamingResponse:
        async def stream() -> Any:
            async with event_bus.subscribe() as queue:
                yield "retry: 3000\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    payload = json.dumps(event, separators=(",", ":"))
                    yield f"event: update\ndata: {payload}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/health")
    async def health() -> JSONResponse:
        stats = await job_manager.stats()
        return JSONResponse(
            {
                "status": "ok",
                "app": settings.app_name,
                "environment": settings.environment,
                "jobs": stats.total,
                "watching": stats.monitoring,
            }
        )

    return app
