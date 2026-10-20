"""Telegram command surface and the /newjob conversation wizard."""

from __future__ import annotations

import contextlib
import logging
import re
from datetime import date, datetime, time, timedelta
from html import escape
from pathlib import Path

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
from bms_assistant.services.bms_links import BmsLink, parse_bms_link
from bms_assistant.services.job_manager import JobManager
from bms_assistant.timeutils import format_local, format_relative, parse_local_datetime

logger = logging.getLogger(__name__)

PROVIDER, BMS_LINK, NAME, VENUE, SHOW_DATE, SHOWTIME, RELEASE, QUANTITY, SEAT, BOOKING_URL = range(
    10
)

GUIDE_IMAGE = Path(__file__).resolve().parents[1] / "web" / "static" / "guide" / "showtimes.png"

HELP_TEXT = (
    "<b>🎟 Ticket Watch</b>\n\n"
    "<b>Commands</b>\n"
    "/newjob — create a watch (paste a BMS link in step 2)\n"
    "/guide — screenshots + where to copy the BMS link\n"
    "/jobs — list watches and open one\n"
    "/job &lt;id&gt; — details and actions\n"
    "/run &lt;id&gt; — start watching\n"
    "/stop &lt;id&gt; — pause a watch\n"
    "/booked &lt;id&gt; — mark as booked\n"
    "/delete &lt;id&gt; — remove a watch\n"
    "/testalert — test the alert path\n"
    "/status — bot and watch stats\n"
    "/cancel — abort the wizard\n\n"
    "Payments stay manual. This bot never stores cards, CVV, UPI IDs, or PINs."
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
    if job.movie_id or job.venue_id:
        codes = " · ".join(
            part
            for part in (
                f"event <code>{escape(job.movie_id)}</code>" if job.movie_id else "",
                f"venue <code>{escape(job.venue_id)}</code>" if job.venue_id else "",
            )
            if part
        )
        lines.append(f"BMS: {codes}")
    if job.show_date:
        lines.append(f"Show date: {job.show_date.strftime('%a, %d %b %Y')}")
    if job.show_at:
        lines.append(f"Show: {escape(format_local(job.show_at, settings.tz))}")
    elif job.has_show_window:
        lines.append(
            f"Show window: {job.window_start.strftime('%I:%M %p').lstrip('0')}"
            f" - {job.window_end.strftime('%I:%M %p').lstrip('0')}"
        )
    if job.seat_prefs.get("category"):
        lines.append(f"Seat category: {escape(str(job.seat_prefs['category']))}")
    if job.seat_prefs.get("max_price"):
        lines.append(f"Max price: ₹{job.seat_prefs['max_price']}")
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


async def guide_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    caption = (
        "<b>Where to get the BMS link</b>\n\n"
        "1. Open the movie on BookMyShow and pick your city + date.\n"
        "2. Tap the showtime. The seat page opens.\n"
        "3. Copy the address-bar URL. It contains:\n"
        "<code>.../seat-layout/ET00521930/ALUC/6296/20261022</code>\n"
        "city · event code · venue code · session · date\n\n"
        "4. Send it to <code>/newjob</code> when asked for the BMS link.\n\n"
        "<b>Showtimes not announced?</b> Copy the movie-page URL instead "
        "(<code>/movies/hyderabad/jailer-2/ET00521930</code>) and choose a time "
        "window such as <code>18:00-23:30</code>.\n\n"
        "Payment stays in BMS. The bot never asks for a card, CVV, UPI ID, or PIN."
    )
    try:
        photo = GUIDE_IMAGE.read_bytes()
    except OSError:
        await update.effective_message.reply_text(
            caption,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return
    await update.effective_message.reply_photo(
        photo=photo,
        caption=caption,
        parse_mode=ParseMode.HTML,
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
        "<b>New watch — provider</b>\nChoose how this watch should alert you:\n\n"
        "🎭 <b>Mock</b> — test the full alert flow. No real network check.\n"
        "⏰ <b>Reminder</b> — a scheduled alert with your BMS link, no scraping.\n\n"
        "Next you'll paste a BMS link. Send /guide for screenshots.",
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
            "<b>BMS watch — link</b>\n"
            "Paste the address-bar URL from the BMS movie page or seat page.\n\n"
            "Example:\n"
            "<code>https://in.bookmyshow.com/movies/hyderabad/seat-layout/"
            "ET00521930/ALUC/6296/20261022</code>\n\n"
            "I'll read the movie, theatre, date, and session codes from it. "
            "No showtimes yet? The movie-page URL is fine — you'll set a time window next.\n\n"
            "Send /skip for a test watch, or /guide for screenshots.",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    return BMS_LINK


def _apply_link(context: ContextTypes.DEFAULT_TYPE, link: BmsLink) -> None:
    data = context.user_data
    data["bms_url"] = link.url
    data["booking_url"] = link.url
    data["movie_id"] = link.event_code
    data["city"] = link.city
    if link.venue_code:
        data["venue_id"] = link.venue_code
    if link.show_date:
        data["show_date"] = link.show_date
    if link.movie_name:
        data["name"] = link.movie_name


async def _ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message:
        await message.reply_text(
            "<b>BMS watch — movie</b>\n"
            "What movie is this? (e.g. <i>Jailer 2</i>)\n"
            "The link didn't include a movie name, so I need a label.",
            parse_mode=ParseMode.HTML,
        )
    return NAME


async def _ask_venue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    data = context.user_data
    hint = ""
    if data.get("venue_id"):
        hint = (
            f"\nThe link says venue <code>{escape(str(data['venue_id']))}</code>. "
            "Send a friendly theatre name, or /skip to keep the code."
        )
    elif data.get("city"):
        hint = (
            f"\nNo theatre code in the link. Send a theatre name, or /skip to "
            f"watch any theatre in <b>{escape(str(data['city']).title())}</b>."
        )
    if message:
        await message.reply_text(
            "<b>BMS watch — theatre</b>\nWhich theatre? (e.g. <i>ALLU Cinemas: Kokapet</i>)" + hint,
            parse_mode=ParseMode.HTML,
        )
    return VENUE


async def _ask_show_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message:
        await message.reply_text(
            "<b>BMS watch — show date</b>\n"
            "Send the date as <code>YYYY-MM-DD</code> (e.g. <code>2026-10-22</code>), "
            "or /skip to watch any date.",
            parse_mode=ParseMode.HTML,
        )
    return SHOW_DATE


async def _ask_showtime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message:
        await message.reply_text(
            "<b>BMS watch — showtime</b>\n"
            "Send a window like <code>18:00-23:30</code> (best when showtimes "
            "aren't announced), an exact time like <code>11:20 PM</code>, or "
            "<code>any</code>.",
            parse_mode=ParseMode.HTML,
        )
    return SHOWTIME


async def _ask_release(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    settings: Settings = context.application.bot_data["settings"]
    message = update.effective_message
    if message:
        await message.reply_text(
            "<b>BMS watch — drop time</b>\n"
            "When do tickets open? Send <code>YYYY-MM-DD HH:MM</code> in "
            f"<code>{escape(settings.timezone)}</code>.\n\n"
            "Don't know? Send /now to start watching now.",
            parse_mode=ParseMode.HTML,
        )
    return RELEASE


async def _ask_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message:
        await message.reply_text(
            "<b>BMS watch — tickets</b>\nHow many seats? (1-10)",
            parse_mode=ParseMode.HTML,
        )
    return QUANTITY


async def _ask_seat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message:
        await message.reply_text(
            "<b>BMS watch — seats</b>\n"
            "Preferred seat category? (e.g. <i>GOLD</i>, <i>PLATINUM</i>)\n"
            "Send /skip for any available category.",
            parse_mode=ParseMode.HTML,
        )
    return SEAT


async def bms_link_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    text = (update.message.text if update.message else "").strip()
    if not text:
        return BMS_LINK
    if text.lower() in {"skip", "/skip"}:
        return await _continue_after_link(update, context)
    try:
        link = parse_bms_link(text)
    except ValueError as exc:
        if message:
            await message.reply_text(
                f"{exc}\n\nSend a BMS movie or seat-page link, /skip for a test "
                "watch, or /guide for screenshots."
            )
        return BMS_LINK

    _apply_link(context, link)
    if message:
        bits = [link.movie_name or link.event_code, link.city_label]
        if link.show_date:
            bits.append(link.show_date.strftime("%a, %d %b %Y"))
        if link.venue_code:
            bits.append(link.venue_code)
        await message.reply_text(
            "Found: <b>" + escape(" · ".join(bits)) + "</b>\n"
            f'<a href="{escape(link.url)}">Open the source page</a>',
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    return await _continue_after_link(update, context)


async def skip_bms_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_message:
        await update.effective_message.reply_text(
            "No BMS link for this one. Continuing as a test watch.",
        )
    return await _continue_after_link(update, context)


async def _continue_after_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data
    if not data.get("name"):
        return await _ask_name(update, context)
    if not data.get("venue"):
        return await _ask_venue(update, context)
    if not data.get("show_date"):
        return await _ask_show_date(update, context)
    return await _ask_showtime(update, context)


async def name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text if update.message else "").strip()
    if not text:
        if update.effective_message:
            await update.effective_message.reply_text("Send a movie name.")
        return NAME
    context.user_data["name"] = text[:120]
    if not context.user_data.get("venue"):
        return await _ask_venue(update, context)
    if not context.user_data.get("show_date"):
        return await _ask_show_date(update, context)
    return await _ask_showtime(update, context)


async def venue_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text if update.message else "").strip()
    if not text:
        if update.effective_message:
            await update.effective_message.reply_text("Send a theatre name.")
        return VENUE
    context.user_data["venue"] = text[:160]
    if not context.user_data.get("show_date"):
        return await _ask_show_date(update, context)
    return await _ask_showtime(update, context)


async def skip_venue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data
    if data.get("venue_id"):
        data["venue"] = str(data["venue_id"])
    elif data.get("city"):
        data["venue"] = f"Any theatre in {str(data['city']).title()}"
    else:
        if update.effective_message:
            await update.effective_message.reply_text(
                "I need a theatre name because there is no venue code in the link.",
            )
        return VENUE
    if not data.get("show_date"):
        return await _ask_show_date(update, context)
    return await _ask_showtime(update, context)


async def show_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text if update.message else "").strip()
    if text.lower() in {"skip", "any", "/skip"}:
        return await skip_show_date(update, context)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        if update.effective_message:
            await update.effective_message.reply_text(
                "Send the date as YYYY-MM-DD (e.g. 2026-10-22), or /skip.",
            )
        return SHOW_DATE
    context.user_data["show_date"] = parsed
    return await _ask_showtime(update, context)


async def skip_show_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("show_date", None)
    return await _ask_showtime(update, context)


def _parse_showtime_choice(
    text: str,
) -> tuple[str, time | None, time | None, time | None]:
    cleaned = text.strip()
    lowered = cleaned.lower()
    if lowered in {"any", "anytime", "skip", "/skip"}:
        return "any", None, None, None

    window = re.fullmatch(r"(\d{1,2}:\d{2})\s*[-\u2013]\s*(\d{1,2}:\d{2})", cleaned)
    if window:
        start_time = datetime.strptime(window.group(1), "%H:%M").time()
        end_time = datetime.strptime(window.group(2), "%H:%M").time()
        if end_time < start_time:
            raise ValueError("The latest showtime must be after the earliest one.")
        return "window", start_time, end_time, None

    upper = cleaned.upper().replace(".", "").strip()
    for fmt in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            parsed = datetime.strptime(upper, fmt)
        except ValueError:
            continue
        return "exact", None, None, parsed.time()

    raise ValueError("Send `any`, a window like 18:00-23:30, or an exact time like 11:20 PM.")


async def showtime_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text if update.message else "").strip()
    try:
        kind, window_start, window_end, show_time = _parse_showtime_choice(text)
    except ValueError as exc:
        if update.effective_message:
            await update.effective_message.reply_text(str(exc))
        return SHOWTIME

    data = context.user_data
    if kind == "exact" and not data.get("show_date"):
        if update.effective_message:
            await update.effective_message.reply_text(
                "I need the show date to pin an exact time. Send it as "
                "<code>YYYY-MM-DD</code> (or /skip, then use a window).",
                parse_mode=ParseMode.HTML,
            )
        return SHOW_DATE

    data["showtime_kind"] = kind
    data["window_start"] = window_start
    data["window_end"] = window_end
    if kind == "exact":
        data["show_time"] = show_time
    else:
        data.pop("show_time", None)
    return await _ask_release(update, context)


async def release_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["release_at"] = utcnow() + timedelta(seconds=15)
    if update.effective_message:
        await update.effective_message.reply_text(
            "Release set to 15 seconds from now (watching starts immediately).",
        )
    return await _ask_quantity(update, context)


async def release_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text if update.message else "").strip()
    settings: Settings = context.application.bot_data["settings"]
    try:
        parsed = parse_local_datetime(text, settings.tz)
    except ValueError as exc:
        if update.effective_message:
            await update.effective_message.reply_text(
                f"{exc}\nTry again, or use /now to start watching now.",
            )
        return RELEASE
    context.user_data["release_at"] = parsed
    return await _ask_quantity(update, context)


async def quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text if update.message else "").strip()
    try:
        quantity = int(text)
    except ValueError:
        if update.effective_message:
            await update.effective_message.reply_text("Send a number between 1 and 10.")
        return QUANTITY
    if not 1 <= quantity <= 10:
        if update.effective_message:
            await update.effective_message.reply_text(
                "Ticket count must be between 1 and 10.",
            )
        return QUANTITY
    context.user_data["quantity"] = quantity
    return await _ask_seat(update, context)


async def seat_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text if update.message else "").strip()
    if text.lower() not in {"skip", "/skip"} and text:
        context.user_data["seat_category"] = text[:40]
    else:
        context.user_data.pop("seat_category", None)
    return await _after_seat(update, context)


async def skip_seat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("seat_category", None)
    return await _after_seat(update, context)


async def _after_seat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data
    if data.get("booking_url"):
        return await _finish_wizard(update, context, data["booking_url"])
    provider = data.get("provider", "mock")
    if provider == "reminder":
        if update.effective_message:
            await update.effective_message.reply_text(
                "Booking URL? This is where the alert sends you.\n"
                "Send a BMS link or any https:// URL.",
            )
        return BOOKING_URL
    return await _finish_wizard(update, context, None)


async def _finish_wizard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    booking_url: str | None,
) -> int:
    data = context.user_data
    provider = data.get("provider", "mock")
    manager: JobManager = context.application.bot_data["job_manager"]
    settings: Settings = context.application.bot_data["settings"]
    show_at = None
    if data.get("show_date") and data.get("show_time"):
        show_at = datetime.combine(data["show_date"], data["show_time"], tzinfo=settings.tz)
    seat_prefs = {}
    if data.get("seat_category"):
        seat_prefs["category"] = data["seat_category"]
    try:
        job = await manager.create_job(
            name=data["name"],
            provider=provider,
            venue=data["venue"],
            quantity=data["quantity"],
            release_at=data["release_at"],
            show_at=show_at,
            booking_url=booking_url,
            chat_id=update.effective_chat.id if update.effective_chat else None,
            auto_start=True,
            movie_id=data.get("movie_id"),
            venue_id=data.get("venue_id"),
            city=data.get("city"),
            show_date=data.get("show_date"),
            window_start=data.get("window_start"),
            window_end=data.get("window_end"),
            seat_prefs=seat_prefs,
        )
    except (JobValidationError, JobNotFoundError) as exc:
        if update.effective_message:
            await update.effective_message.reply_text(f"Could not create watch: {exc}")
        context.user_data.clear()
        return ConversationHandler.END

    if update.effective_message:
        await update.effective_message.reply_text(
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
        if update.effective_message:
            await update.effective_message.reply_text(
                "A scheduled reminder needs a booking URL. Send it, or /cancel.",
            )
        return BOOKING_URL
    if booking_url:
        with contextlib.suppress(ValueError):
            _apply_link(context, parse_bms_link(booking_url))
    return await _finish_wizard(update, context, booking_url)


async def skip_booking_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    provider = context.user_data.get("provider", "mock")
    if provider == "reminder":
        if update.effective_message:
            await update.effective_message.reply_text(
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
    application.add_handler(CommandHandler("guide", guide_cmd))
    application.add_handler(CallbackQueryHandler(handle_job_callback, pattern=r"^(action:|view:)"))

    wizard = ConversationHandler(
        entry_points=[CommandHandler("newjob", newjob_start)],
        states={
            PROVIDER: [CallbackQueryHandler(provider_chosen, pattern=r"^provider:")],
            BMS_LINK: [
                CommandHandler("skip", skip_bms_link),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bms_link_received),
            ],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name_received)],
            VENUE: [
                CommandHandler("skip", skip_venue),
                MessageHandler(filters.TEXT & ~filters.COMMAND, venue_received),
            ],
            SHOW_DATE: [
                CommandHandler("skip", skip_show_date),
                MessageHandler(filters.TEXT & ~filters.COMMAND, show_date_received),
            ],
            SHOWTIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, showtime_received),
            ],
            RELEASE: [
                CommandHandler("now", release_now),
                MessageHandler(filters.TEXT & ~filters.COMMAND, release_received),
            ],
            QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_received)],
            SEAT: [
                CommandHandler("skip", skip_seat),
                MessageHandler(filters.TEXT & ~filters.COMMAND, seat_received),
            ],
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
