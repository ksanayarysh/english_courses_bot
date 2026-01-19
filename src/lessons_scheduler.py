from __future__ import annotations

import asyncio
from telegram import Bot


def _format_welcome(course: dict) -> str:
    w = (course.get("welcome_video_url") or "").strip()
    if w:
        return f"👋 Добро пожаловать!\n\n🎥 Приветственное видео:\n{w}\n"
    return "👋 Добро пожаловать!\n"


def _format_lesson(lesson: dict) -> str:
    video = lesson["video_url"]
    mat = lesson.get("materials_url")
    idx = lesson.get("lesson_index")
    title = lesson.get("title") or ""
    text = f"🎬 Урок {idx}: {title}\n\nВидео:\n{video}\n"
    if mat:
        text += f"\n📄 Материалы (PDF/DOC):\n{mat}\n"
    return text


async def send_welcome_and_lesson1(*, bot: Bot, db: "Db", user_id: int, course_id: str) -> None:
    course = db.get_course(course_id=course_id)
    if not course:
        raise RuntimeError(f"Course not found: {course_id}")

    enr = db.ensure_enrollment(user_id=user_id, course_id=course_id)

    await bot.send_message(chat_id=user_id, text=_format_welcome(course))

    next_idx = int(enr.get("next_lesson_index") or 1)
    if next_idx > 1:
        return

    lesson1 = db.get_lesson(course_id=course_id, lesson_index=1)
    if not lesson1:
        await bot.send_message(chat_id=user_id, text="Урок 1 пока не добавлен администратором.")
        return

    await bot.send_message(chat_id=user_id, text=_format_lesson(lesson1))

    interval = int(course.get("lesson_interval_days") or 7)
    db.advance_enrollment_after_sent(
        user_id=user_id,
        course_id=course_id,
        next_lesson_index=2,
        lesson_interval_days=interval,
    )


async def lessons_scheduler_loop(*, bot: Bot, db: "Db", poll_seconds: int = 60) -> None:
    while True:
        try:
            due = db.list_due_enrollments(limit=50)
            for e in due:
                user_id = int(e["user_id"])
                course_id = e["course_id"]
                next_idx = int(e["next_lesson_index"])

                course = db.get_course(course_id=course_id)
                if not course:
                    continue

                lesson = db.get_lesson(course_id=course_id, lesson_index=next_idx)
                if not lesson:
                    db.advance_enrollment_after_sent(
                        user_id=user_id,
                        course_id=course_id,
                        next_lesson_index=next_idx,
                        lesson_interval_days=1,
                    )
                    continue

                await bot.send_message(chat_id=user_id, text=_format_lesson(lesson))

                interval = int(course.get("lesson_interval_days") or 7)
                db.advance_enrollment_after_sent(
                    user_id=user_id,
                    course_id=course_id,
                    next_lesson_index=next_idx + 1,
                    lesson_interval_days=interval,
                )
        except Exception:
            pass

        await asyncio.sleep(poll_seconds)
