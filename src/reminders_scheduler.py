from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class ReminderConfig:
    """Configuration for live-session reminders.

    * poll_seconds: how often we scan DB for due reminders
    * course_timezone: IANA timezone name for formatting (e.g. America/Sao_Paulo)
    """

    poll_seconds: int = 60
    course_timezone: str = "UTC"


def _fmt_dt(dt_utc: datetime, tz_name: str) -> str:
    # DB stores TIMESTAMPTZ, we treat it as UTC.
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)

    if tz_name and ZoneInfo is not None:
        try:
            tz = ZoneInfo(tz_name)
            local = dt_utc.astimezone(tz)
            return local.strftime("%Y-%m-%d %H:%M") + f" ({tz_name})"
        except Exception:
            pass

    return dt_utc.astimezone(UTC).strftime("%Y-%m-%d %H:%M") + " (UTC)"


async def reminders_scheduler_loop(*, bot, db, cfg: ReminderConfig) -> None:
    """Background task: sends 24h / 1h / 15m reminders for scheduled live sessions.

    Requirements on Db:
      - list_live_sessions_due_24h(now)
      - list_live_sessions_due_1h(now)
      - list_live_sessions_due_15m(now)
      - mark_live_session_reminded(session_id, kind)

    Each returned row should have at least:
      id, user_id, starts_at, title, meeting_url
    """

    while True:
        try:
            now = now_utc()

            # Order matters: send the closest reminders first.
            due_15m = db.list_live_sessions_due_15m(now)
            for s in due_15m:
                await _send(bot, db, cfg, s, kind="15m")

            due_1h = db.list_live_sessions_due_1h(now)
            for s in due_1h:
                await _send(bot, db, cfg, s, kind="1h")

            due_24h = db.list_live_sessions_due_24h(now)
            for s in due_24h:
                await _send(bot, db, cfg, s, kind="24h")

        except Exception:
            # Don't crash the whole service because a reminder failed.
            # Railway restarts are annoying enough without self-inflicted ones.
            pass

        await asyncio.sleep(max(10, int(cfg.poll_seconds)))


async def _send(bot, db, cfg: ReminderConfig, session: dict, *, kind: str) -> None:
    session_id = int(session["id"])
    user_id = int(session["user_id"])
    starts_at = session["starts_at"]
    title = str(session.get("title") or "Практика")
    meeting_url = str(session.get("meeting_url") or "").strip()

    when = _fmt_dt(starts_at, cfg.course_timezone)

    if kind == "24h":
        lead = "⏰ Завтра"
    elif kind == "1h":
        lead = "⏰ Через час"
    else:
        lead = "⏰ Через 15 минут"

    text = f"{lead} занятие: <b>{title}</b>\nВремя: <b>{when}</b>"
    if meeting_url:
        text += f"\nСсылка: {meeting_url}"

    # Send first, mark after success.
    await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
    db.mark_live_session_reminded(session_id, kind)
