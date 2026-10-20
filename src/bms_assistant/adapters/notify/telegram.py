"""Telegram notifier with a direct handoff button."""

from __future__ import annotations

from html import escape
from zoneinfo import ZoneInfo

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from bms_assistant.ports.notifiers import Notification
from bms_assistant.timeutils import format_local


class TelegramNotifier:
    def __init__(
        self,
        bot: Bot,
        default_chat_id: int | None = None,
        tz: ZoneInfo | None = None,
    ) -> None:
        self._bot = bot
        self._default_chat_id = default_chat_id
        self._tz = tz

    @property
    def name(self) -> str:
        return "telegram"

    async def send(self, notification: Notification) -> None:
        job = notification.job
        chat_id = job.chat_id if job and job.chat_id else self._default_chat_id
        if not chat_id:
            return

        lines = [f"<b>{escape(notification.title)}</b>", "", escape(notification.body)]
        if job:
            lines.extend(
                [
                    "",
                    f"<b>Watch:</b> {escape(job.name)}",
                    f"<b>Venue:</b> {escape(job.venue)}",
                    f"<b>Tickets:</b> {job.quantity}",
                ]
            )
            if job.movie_id or job.venue_id:
                codes = " · ".join(
                    part
                    for part in (
                        f"event <code>{escape(job.movie_id)}</code>" if job.movie_id else "",
                        f"venue <code>{escape(job.venue_id)}</code>" if job.venue_id else "",
                    )
                    if part
                )
                lines.append(f"<b>BMS:</b> {codes}")
            if job.show_date:
                lines.append(f"<b>Show date:</b> {job.show_date.strftime('%a, %d %b %Y')}")
            if job.show_at:
                show = format_local(job.show_at, self._tz) if self._tz else job.show_at
                lines.append(f"<b>Show:</b> {escape(str(show))}")
            elif job.has_show_window:
                start = job.window_start.strftime("%I:%M %p").lstrip("0")
                end = job.window_end.strftime("%I:%M %p").lstrip("0")
                lines.append(f"<b>Show window:</b> {escape(start)} - {escape(end)}")
            if job.seat_prefs.get("category"):
                lines.append(f"<b>Seat category:</b> {escape(str(job.seat_prefs['category']))}")
            if job.seat_prefs.get("max_price"):
                lines.append(f"<b>Max price:</b> ₹{job.seat_prefs['max_price']}")
            if job.release_at:
                release = format_local(job.release_at, self._tz) if self._tz else job.release_at
                lines.append(f"<b>Release:</b> {escape(str(release))}")
            lines.extend(
                [
                    f"<b>ID:</b> <code>{job.short_id}</code>",
                    "",
                    "Open the link, choose seats, and pay in BMS. "
                    "Never share an OTP, CVV, or UPI PIN.",
                ]
            )

        buttons: list[list[InlineKeyboardButton]] = []
        if notification.url:
            is_bms = "bookmyshow.com" in notification.url
            buttons.append(
                [
                    InlineKeyboardButton(
                        "Open BMS seats ↗" if is_bms else "Open booking ↗",
                        url=notification.url,
                    )
                ]
            )
        if job:
            buttons.append(
                [
                    InlineKeyboardButton("Mark booked", callback_data=f"action:booked:{job.id}"),
                    InlineKeyboardButton("Pause", callback_data=f"action:stop:{job.id}"),
                ]
            )
            buttons.append([InlineKeyboardButton("Details", callback_data=f"view:{job.id}")])

        await self._bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            parse_mode=ParseMode.HTML,
            disable_notification=False,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )
