"""Telegram command surface and the /newjob conversation wizard."""

from __future__ import annotations

import logging
from datetime import timedelta
from html import escape

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from bms_assistant.config import Settings
from bms_assistant.domain.errors import JobNotFoundError, JobValidationError
from bms_assistant.domain.models import Job, JobStatus, utcnow
from bms_assistant.services.job_manager import JobManager
from bms_assistant.timeutils import format_local, format_relative, parse_local_datetime

logger = logging.getLogger(__name__)

PROVIDER, NAME, VENUE, RELEASE, QUANTITY, BOOKING_URL = range(6)

HELP_TEXT = (
    "<b>🎟 Ticket Watch</b>\n\n"
    "<b>Commands</b>\n"
    "/newjob — create a watch with the wizard\n"
    "/jobs — list watches and open one\n"
    "/job &lt;id&gt; — details and actions\n"
    "/run &lt;id&gt; — start watching\n"
    "/stop &lt;id&gt; — pause a watch\n"
    "/booked &lt;id&gt; — mark as booked\n"
    "/delete &lt;id&gt; — remove a watch\n"
    "/testalert — test the alert path\n"
    "/status — bot and watch stats\n"
    "/cancel — abort the wizard\n\n"
    "Payments stay manual. This bot only watches and hands off."
)


def _job_text(job: Job, settings: Settings) -> str:
    status = escape(job.status_label)
    lines = [
        f"<b>{escape(job.name)}</b>",
        f"Status: <b>{status}</b>",
        f"Provider: <code>{escape(job.provider)}</code>",
        f"Venue: {escape(job.venue)}",
        f"Tickets: {job.quantity}",
        f"Release: {escape(format_local(job.release_at, settings.tz))}",
    ]
    if job.show_at:
        lines.append(f"Show: {escape(format_local(job.show_at, settings.tz))}")
    if job.release_at:
        lines.append(f"Release in: {escape(format_relative(job.release_at))}")
    if job.booking_url:
        lines.append(f'Handoff: <a href="{escape(job.booking_url)}">open booking page</a>')
    if job.error:
        lines.append(f"Error: <code>{escape(job.error)}</code>")
    lines.append(f"Checks: {job.check_count} · ID: <code>{job.short_id}</code>")
    return "\n".join(lines)


def _job_keyboard(job: Job) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if job.booking_url:
        rows.append([InlineKeyboardButton("Open booking ↗", url=job.booking_url)])
    if job.status == JobStatus.MONITORING:
        rows.append([InlineKeyboardButton("Pause", callback_data=f"action:stop:{job.id}")])
    elif job.status != JobStatus.BOOKED:
        rows.append([InlineKeyboardButton("Start", callback_data=f"action:start:{job.id}")])
    rows.append(
        [
            InlineKeyboardButton("Check now", callback_data=f"action:check:{job.id}"),
            InlineKeyboardButton("Mark booked", callback_data=f"action:booked:{job.id}"),
        ]
    )
    rows.append([InlineKeyboardButton("Delete", callback_data=f"action:delete:{job.id}")])
    return InlineKeyboardMarkup(rows)


async def _safe_edit(
    query: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup | None = None
) -> None:
    try:
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    chat_id = update.effective_chat.id if update.effective_chat else "unknown"
    await update.effective_message.reply_text(
        f"{HELP_TEXT}\n\nYour chat id: <code>{chat_id}</code>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def _require_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Let only the configured chat control the bot.

    ``/start`` and ``/help`` stay open so the owner can discover their chat id
    during setup. Every other update from another chat is dropped.
    """
    allowed_chat_id: int | None = context.application.bot_data.get("allowed_chat_id")
    if allowed_chat_id is None:
        return

    chat = update.effective_chat
    if chat is not None and chat.id == allowed_chat_id:
        return

    message = update.effective_message
    text = (message.text or "").strip() if message else ""
    command = text.split()[0].split("@", 1)[0].lower() if text.startswith("/") else ""
    if command in {"/start", "/help"}:
        return

    if update.callback_query is not None:
        await update.callback_query.answer("This bot is private.", show_alert=True)
    elif message is not None:
        await message.reply_text("This bot is private.")
    logger.warning("Blocked Telegram update from chat %s", chat.id if chat else "unknown")
    raise ApplicationHandlerStop


async def jobs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    jobs = await context.application.bot_data["job_manager"].list_jobs()
    if not jobs:
        await update.effective_message.reply_text(
            "No watches yet. Use /newjob to create one.",
        )
        return

    lines = ["<b>Watchlist</b>", ""]
    keyboard: list[list[InlineKeyboardButton]] = []
    for job in jobs[:12]:
        lines.append(
            f"<code>{job.short_id}</code> <b>{escape(job.name)}</b> — {escape(job.status_label)}"
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"{job.status_label} · {job.name[:28]}",
                    callback_data=f"view:{job.id}",
                )
            ]
        )
    if len(jobs) > 12:
        lines.append(f"…and {len(jobs) - 12} more. Use /job &lt;id&gt;.")

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True,
    )


async def job_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /job <id>")
        return
    try:
        job = await context.application.bot_data["job_manager"].resolve_job(context.args[0])
    except (JobNotFoundError, JobValidationError) as exc:
        await update.effective_message.reply_text(str(exc))
        return
    settings = context.application.bot_data["settings"]
    await update.effective_message.reply_text(
        _job_text(job, settings),
        parse_mode=ParseMode.HTML,
        reply_markup=_job_keyboard(job),
        disable_web_page_preview=True,
    )


async def test_alert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "🔔 <b>Test alert</b>\nIf you saw the notification and heard your phone, "
        "the alert path works. This is not a real ticket drop.",
        parse_mode=ParseMode.HTML,
        disable_notification=False,
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    manager: JobManager = context.application.bot_data["job_manager"]
    stats = await manager.stats()
    settings: Settings = context.application.bot_data["settings"]
    await update.effective_message.reply_text(
        (
            "<b>Ticket Watch status</b>\n"
            f"Watching: <b>{stats.monitoring}</b>\n"
            f"Live now: <b>{stats.triggered}</b>\n"
            f"Booked: <b>{stats.booked}</b>\n"
            f"Total watches: {stats.total}\n"
            f"Timezone: <code>{escape(settings.timezone)}</code>"
        ),
        parse_mode=ParseMode.HTML,
    )


async def newjob_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_message is None:
        return ConversationHandler.END
    context.user_data.clear()
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎭 Mock drop (dev)", callback_data="provider:mock"),
                InlineKeyboardButton("⏰ Scheduled reminder", callback_data="provider:reminder"),
            ]
        ]
    )
    await update.effective_message.reply_text(
        "<b>New watch — step 1/5</b>\nChoose a provider:\n\n"
        "Mock is for testing the full alert flow. Reminder is a plain scheduled "
        "alert with your booking link.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
    return PROVIDER


async def provider_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return PROVIDER
    await query.answer()
    provider = query.data.split(":", 1)[1] if query.data else "mock"
    context.user_data["provider"] = provider
    label = "Mock drop" if provider == "mock" else "Scheduled reminder"
    await _safe_edit(query, f"Provider: <b>{escape(label)}</b>")
    if update.effective_message:
        await update.effective_message.reply_text(
            "<b>Step 2/5</b>\nWhat should I call this watch? (e.g. <i>Dune 3 — opening night</i>)",
            parse_mode=ParseMode.HTML,
        )
    return NAME


async def name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text if update.message else "").strip()
    if not text:
        await update.message.reply_text("Send a name.")  # type: ignore[union-attr]
        return NAME
    context.user_data["name"] = text
    await update.message.reply_text(  # type: ignore[union-attr]
        "<b>Step 3/5</b>\nVenue or platform? (e.g. PVR ICON, Hyderabad)",
        parse_mode=ParseMode.HTML,
    )
    return VENUE


async def venue_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text if update.message else "").strip()
    if not text:
        await update.message.reply_text("Send a venue.")  # type: ignore[union-attr]
        return VENUE
    context.user_data["venue"] = text
    settings: Settings = context.application.bot_data["settings"]
    await update.message.reply_text(  # type: ignore[union-attr]
        "<b>Step 4/5</b>\nWhen does it drop? Send "
        "<code>YYYY-MM-DD HH:MM</code> in "
        f"<code>{escape(settings.timezone)}</code>, or /now for 15 seconds from now.",
        parse_mode=ParseMode.HTML,
    )
    return RELEASE


async def release_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["release_at"] = utcnow() + timedelta(seconds=15)
    if update.message:
        await update.message.reply_text(
            "<b>Step 5/5</b>\nRelease set to 15 seconds from now.\nHow many tickets? (1-10)",
            parse_mode=ParseMode.HTML,
        )
    return QUANTITY


async def release_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text if update.message else "").strip()
    settings: Settings = context.application.bot_data["settings"]
    try:
        parsed = parse_local_datetime(text, settings.tz)
    except ValueError as exc:
        await update.message.reply_text(  # type: ignore[union-attr]
            f"{exc}\nTry again, or use /now for a quick dev test.",
        )
        return RELEASE
    context.user_data["release_at"] = parsed
    await update.message.reply_text(  # type: ignore[union-attr]
        "<b>Step 5/5</b>\nHow many tickets? (1-10)",
        parse_mode=ParseMode.HTML,
    )
    return QUANTITY


async def quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text if update.message else "").strip()
    try:
        quantity = int(text)
    except ValueError:
        await update.message.reply_text("Send a number between 1 and 10.")  # type: ignore[union-attr]
        return QUANTITY
    if not 1 <= quantity <= 10:
        await update.message.reply_text("Ticket count must be between 1 and 10.")  # type: ignore[union-attr]
        return QUANTITY
    context.user_data["quantity"] = quantity

    provider = context.user_data.get("provider", "mock")
    if provider == "reminder":
        prompt = (
            "Booking URL? This is where the alert will send you.\n"
            "It must start with http:// or https://."
        )
    else:
        prompt = "Booking URL? Optional for mock — send /skip to use a generated demo link."
    await update.message.reply_text(prompt)  # type: ignore[union-attr]
    return BOOKING_URL


async def _finish_wizard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    booking_url: str | None,
) -> int:
    provider = context.user_data.get("provider", "mock")
    manager: JobManager = context.application.bot_data["job_manager"]
    try:
        job = await manager.create_job(
            name=context.user_data["name"],
            provider=provider,
            venue=context.user_data["venue"],
            quantity=context.user_data["quantity"],
            release_at=context.user_data["release_at"],
            booking_url=booking_url,
            chat_id=update.effective_chat.id if update.effective_chat else None,
            auto_start=True,
        )
    except (JobValidationError, JobNotFoundError) as exc:
        await update.message.reply_text(f"Could not create watch: {exc}")  # type: ignore[union-attr]
        context.user_data.clear()
        return ConversationHandler.END

    settings: Settings = context.application.bot_data["settings"]
    await update.message.reply_text(  # type: ignore[union-attr]
        "✅ <b>Watch created and started</b>\n\n" + _job_text(job, settings),
        parse_mode=ParseMode.HTML,
        reply_markup=_job_keyboard(job),
        disable_web_page_preview=True,
    )
    context.user_data.clear()
    return ConversationHandler.END


async def booking_url_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text if update.message else "").strip()
    provider = context.user_data.get("provider", "mock")
    booking_url = None if text.lower() in {"skip", "/skip"} else text
    if provider == "reminder" and not booking_url:
        await update.message.reply_text(  # type: ignore[union-attr]
            "A scheduled reminder needs a booking URL. Send it, or /cancel.",
        )
        return BOOKING_URL
    return await _finish_wizard(update, context, booking_url)


async def skip_booking_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    provider = context.user_data.get("provider", "mock")
    if provider == "reminder":
        await update.message.reply_text(  # type: ignore[union-attr]
            "A scheduled reminder needs a booking URL. Send it, or /cancel.",
        )
        return BOOKING_URL
    return await _finish_wizard(update, context, None)


async def cancel_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.effective_message:
        await update.effective_message.reply_text("Cancelled.")
    return ConversationHandler.END


async def handle_job_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    manager: JobManager = context.application.bot_data["job_manager"]
    settings: Settings = context.application.bot_data["settings"]

    if query.data.startswith("view:"):
        try:
            job = await manager.resolve_job(query.data.split(":", 1)[1])
        except (JobNotFoundError, JobValidationError) as exc:
            await query.answer(str(exc)[:180], show_alert=True)
            return
        await query.answer()
        await _safe_edit(query, _job_text(job, settings), _job_keyboard(job))
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Unknown action.")
        return
    _, action, job_id = parts
    try:
        job = await manager.get_job(job_id)
        if action == "start":
            job = await manager.start(job.id)
            await query.answer("Watching.")
        elif action == "stop":
            job = await manager.stop(job.id)
            await query.answer("Paused.")
        elif action == "booked":
            job = await manager.mark_booked(job.id)
            await query.answer("Marked as booked.")
        elif action == "delete":
            name = job.name
            await manager.delete(job.id)
            await query.answer("Deleted.")
            await _safe_edit(query, f"🗑 Deleted watch <b>{escape(name)}</b>.")
            return
        elif action == "check":
            result = await manager.check_now(job.id)
            await query.answer("Check complete.")
            if update.effective_message:
                await update.effective_message.reply_text(
                    result.detail or "Check complete.",
                    disable_web_page_preview=True,
                )
            job = await manager.get_job(job.id)
        else:
            await query.answer("Unknown action.")
            return
    except (JobNotFoundError, JobValidationError) as exc:
        await query.answer(str(exc)[:180], show_alert=True)
        return
    except Exception as exc:
        logger.exception("Telegram callback failed")
        await query.answer(f"Action failed: {exc}"[:180], show_alert=True)
        return

    await _safe_edit(query, _job_text(job, settings), _job_keyboard(job))


async def _command_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
) -> None:
    if update.effective_message is None:
        return
    if not context.args:
        await update.effective_message.reply_text(f"Usage: /{action} <id>")
        return
    manager: JobManager = context.application.bot_data["job_manager"]
    try:
        job = await manager.resolve_job(context.args[0])
        if action == "run":
            job = await manager.start(job.id)
            response = f"▶ Watching <b>{escape(job.name)}</b>."
        elif action == "stop":
            job = await manager.stop(job.id)
            response = f"⏸ Paused <b>{escape(job.name)}</b>."
        elif action == "booked":
            job = await manager.mark_booked(job.id)
            response = f"✅ Marked <b>{escape(job.name)}</b> as booked."
        elif action == "delete":
            name = job.name
            await manager.delete(job.id)
            await update.effective_message.reply_text(
                f"🗑 Deleted <b>{escape(name)}</b>.", parse_mode=ParseMode.HTML
            )
            return
        else:
            response = "Unknown action."
    except (JobNotFoundError, JobValidationError) as exc:
        await update.effective_message.reply_text(str(exc))
        return
    await update.effective_message.reply_text(
        response,
        parse_mode=ParseMode.HTML,
        reply_markup=_job_keyboard(job),
        disable_web_page_preview=True,
    )


async def run_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _command_action(update, context, "run")


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _command_action(update, context, "stop")


async def booked_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _command_action(update, context, "booked")


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _command_action(update, context, "delete")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Telegram update failed", exc_info=context.error)


def register_handlers(
    application: Application, settings: Settings, job_manager: JobManager
) -> None:
    application.bot_data["settings"] = settings
    application.bot_data["job_manager"] = job_manager
    application.bot_data["allowed_chat_id"] = settings.telegram_chat_id

    application.add_handler(TypeHandler(Update, _require_authorized), group=-1)
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("help", start_cmd))
    application.add_handler(CommandHandler("jobs", jobs_cmd))
    application.add_handler(CommandHandler("job", job_cmd))
    application.add_handler(CommandHandler("run", run_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("booked", booked_cmd))
    application.add_handler(CommandHandler("delete", delete_cmd))
    application.add_handler(CommandHandler("testalert", test_alert_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CallbackQueryHandler(handle_job_callback, pattern=r"^(action:|view:)"))

    wizard = ConversationHandler(
        entry_points=[CommandHandler("newjob", newjob_start)],
        states={
            PROVIDER: [CallbackQueryHandler(provider_chosen, pattern=r"^provider:")],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name_received)],
            VENUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, venue_received)],
            RELEASE: [
                CommandHandler("now", release_now),
                MessageHandler(filters.TEXT & ~filters.COMMAND, release_received),
            ],
            QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_received)],
            BOOKING_URL: [
                CommandHandler("skip", skip_booking_url),
                MessageHandler(filters.TEXT & ~filters.COMMAND, booking_url_received),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_wizard)],
        allow_reentry=True,
    )
    application.add_handler(wizard)
    application.add_error_handler(error_handler)
