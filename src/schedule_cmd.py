from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from telegram import Update
from telegram.ext import ContextTypes


def _parse_dt(date_s: str, time_s: str, tz_name: str) -> datetime:
    dt_naive = datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M")

    if tz_name and ZoneInfo is not None:
        try:
            tz = ZoneInfo(tz_name)
            dt_local = dt_naive.replace(tzinfo=tz)
            return dt_local.astimezone(timezone.utc)
        except Exception:
            pass

    # Fallback: treat as UTC
    return dt_naive.replace(tzinfo=timezone.utc)


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: schedule a live session + reminders.

    Usage:
      /schedule <user_id> <YYYY-MM-DD> <HH:MM> <meeting_url_or_-> [title...]

    Examples:
      /schedule 123 2026-01-23 19:00 https://zoom.us/j/xxx Практика #1
      /schedule 123 2026-01-23 19:00 - Практика без ссылки

    Notes:
      - Time is interpreted in COURSE_TZ (env), stored as UTC in DB.
      - meeting_url: put '-' if not available yet.
    """

    cfg = context.bot_data.get("cfg")
    repo = context.bot_data.get("live_repo")

    user = update.effective_user
    if not user or not cfg or not repo:
        return

    if user.id not in cfg.admin_ids:
        await update.effective_message.reply_text("Нет прав.")
        return

    if len(context.args) < 4:
        await update.effective_message.reply_text(
            "Использование: /schedule <user_id> <YYYY-MM-DD> <HH:MM> <meeting_url_or_-> [title...]"
        )
        return

    try:
        uid = int(context.args[0])
        date_s = context.args[1]
        time_s = context.args[2]
        meeting_url = context.args[3]
        title = " ".join(context.args[4:]).strip() or "Практика"

        if meeting_url == "-":
            meeting_url = ""

        starts_at_utc = _parse_dt(date_s, time_s, getattr(cfg, "course_tz", "UTC"))

        session_id = repo.add_session(
            user_id=uid,
            starts_at=starts_at_utc,
            title=title,
            meeting_url=meeting_url,
        )
    except ValueError:
        await update.effective_message.reply_text("Неверные аргументы (user_id / дата / время).")
        return

    await update.effective_message.reply_text(
        f"✅ Запланировано. session_id={session_id} user_id={uid} starts_at(UTC)={starts_at_utc.isoformat()}"
    )
