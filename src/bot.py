from __future__ import annotations

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from payments.mercadopago_pix import MercadoPagoPixProvider
from payments.service import PaymentService
from config import load_config
from db import Db, now_utc


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💳 Оплатить", callback_data="pay")],
            [InlineKeyboardButton("🎬 Уроки (после оплаты)", callback_data="access")],
            [InlineKeyboardButton("🧾 Статус", callback_data="status")],
        ]
    )


def format_status(db: Db, user_id: int) -> str:
    ok, expires_at, reason = db.is_subscribed(user_id)
    if ok:
        if expires_at:
            exp = expires_at.strftime("%Y-%m-%d %H:%M UTC")
            return f"✅ Доступ активен до: <b>{exp}</b>"
        return "✅ Доступ активен: <b>без срока</b>"
    if reason == "expired" and expires_at:
        exp = expires_at.strftime("%Y-%m-%d %H:%M UTC")
        return f"⛔ Доступ истёк: <b>{exp}</b>"
    if reason == "revoked":
        return "⛔ Доступ отключён админом."
    return "⛔ Доступа нет. Нужна оплата."


async def create_invite_link(context: ContextTypes.DEFAULT_TYPE, channel_id: str, user_id: int) -> str:
    expire_date = int((now_utc().timestamp()) + 2 * 60 * 60)  # 2 hours
    invite = await context.bot.create_chat_invite_link(
        chat_id=channel_id,
        name=f"access_{user_id}_{int(now_utc().timestamp())}",
        member_limit=1,
        expire_date=expire_date,
    )
    return invite.invite_link


# ---------------- Handlers ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = user.first_name if user else "там"
    text = (
        f"Привет, {name}.\n\n"
        "Демо клуба:\n"
        "1) Оплата\n"
        "2) Доступ в приватный канал с видео\n\n"
        "Жми кнопку."
    )
    await update.effective_message.reply_text(text, reply_markup=main_menu())


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    db: Db = context.bot_data["db"]
    user = update.effective_user
    if not user:
        return
    text = "🧾 <b>Статус</b>\n\n" + format_status(db, user.id)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu())


async def cmd_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    db: Db = context.bot_data["db"]
    user = update.effective_user
    if not user:
        return

    ok, _, _ = db.is_subscribed(user.id)
    if not ok:
        await update.effective_message.reply_text("Доступ закрыт. Сначала оплата.", reply_markup=main_menu())
        return

    try:
        link = await create_invite_link(context, cfg.channel_id, user.id)
    except Exception as e:
        await update.effective_message.reply_text(
            "Не смог создать инвайт-ссылку. Проверь права бота в канале.\n"
            f"Ошибка: {type(e).__name__}: {str(e)[:180]}"
        )
        return

    text = (
        "🎬 <b>Доступ открыт</b>\n\n"
        "Персональная одноразовая ссылка (2 часа):\n"
        f"{link}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu())


async def cmd_pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Db = context.bot_data["db"]
    user = update.effective_user
    if not user:
        return

    payment_id = db.create_fake_payment(user.id)
    text = (
        "💳 <b>Оплата (демо)</b>\n\n"
        "В реальной версии тут будет checkout/инвойс платежной системы.\n"
        f"Демо-платёж: <code>{payment_id}</code>\n\n"
        "Нажми «Сымитировать оплату», чтобы показать flow."
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Сымитировать оплату", callback_data=f"simulate_paid:{payment_id}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu")],
        ]
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    user = update.effective_user
    if not user:
        return
    is_admin = user.id in cfg.admin_ids
    await update.effective_message.reply_text(
        f"user_id: <code>{user.id}</code>\nadmin: <b>{'yes' if is_admin else 'no'}</b>",
        parse_mode=ParseMode.HTML,
    )


# ---- Admin commands ----
async def cmd_grant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    db: Db = context.bot_data["db"]
    user = update.effective_user
    if not user or user.id not in cfg.admin_ids:
        await update.effective_message.reply_text("Нет прав.")
        return

    if not context.args:
        await update.effective_message.reply_text("Использование: /grant <user_id> [days]")
        return

    try:
        uid = int(context.args[0])
        days = int(context.args[1]) if len(context.args) > 1 else None
    except ValueError:
        await update.effective_message.reply_text("Неверные аргументы.")
        return

    db.set_subscription(uid, active=True, days=days)
    await update.effective_message.reply_text(f"✅ Выдан доступ {uid} " + (f"на {days} дней." if days else "без срока."))


async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    db: Db = context.bot_data["db"]
    user = update.effective_user
    if not user or user.id not in cfg.admin_ids:
        await update.effective_message.reply_text("Нет прав.")
        return

    if not context.args:
        await update.effective_message.reply_text("Использование: /revoke <user_id>")
        return

    try:
        uid = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Неверный user_id.")
        return

    db.set_subscription(uid, active=False)
    await update.effective_message.reply_text(f"⛔ Доступ отключён для {uid}.")


async def cmd_list_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    db: Db = context.bot_data["db"]
    user = update.effective_user
    if not user or user.id not in cfg.admin_ids:
        await update.effective_message.reply_text("Нет прав.")
        return

    rows = db.list_active(limit=50)
    if not rows:
        await update.effective_message.reply_text("Активных подписок нет.")
        return

    lines = ["<b>Активные подписки (до 50):</b>"]
    for r in rows:
        expires = r["expires_at"].isoformat() if r["expires_at"] else "forever"
        lines.append(f"• <code>{r['user_id']}</code> expires: <b>{expires}</b>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# -------------- Callbacks --------------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    db: Db = context.bot_data["db"]

    q = update.callback_query
    if not q:
        return
    await q.answer()

    data = q.data or ""
    user = update.effective_user
    uid = user.id if user else None

    if data == "menu":
        await q.edit_message_text("Меню:", reply_markup=main_menu())
        return

    if data == "pay":
        if not uid:
            return
        payment_id = db.create_fake_payment(uid)
        text = (
            "💳 <b>Оплата (демо)</b>\n\n"
            "В реальной версии тут будет checkout/инвойс платежной системы.\n"
            f"Демо-платёж: <code>{payment_id}</code>\n\n"
            "Нажми «Сымитировать оплату», чтобы показать flow."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ Сымитировать оплату", callback_data=f"simulate_paid:{payment_id}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu")],
            ]
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("simulate_paid:"):
        if not uid:
            return
        payment_id = data.split(":", 1)[1].strip()
        paid_user_id = db.mark_fake_payment_paid(payment_id)
        if paid_user_id is None:
            await q.edit_message_text("Платёж не найден.", reply_markup=main_menu())
            return

        db.set_subscription(paid_user_id, active=True, days=30)
        await q.edit_message_text(
            "✅ <b>Оплата подтверждена (демо)</b>\n\nДоступ выдан на 30 дней. Жми «Уроки».",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )
        return

    if data == "access":
        if not uid:
            return
        ok, _, _ = db.is_subscribed(uid)
        if not ok:
            await q.edit_message_text("Доступ закрыт. Сначала оплата.", reply_markup=main_menu())
            return
        try:
            link = await create_invite_link(context, cfg.channel_id, uid)
        except Exception as e:
            await q.edit_message_text(
                f"Не смог создать инвайт-ссылку. Проверь права бота.\nОшибка: {type(e).__name__}: {str(e)[:180]}",
                reply_markup=main_menu(),
            )
            return
        await q.edit_message_text(
            "🎬 <b>Доступ открыт</b>\n\n"
            "Персональная одноразовая ссылка (2 часа):\n"
            f"{link}",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )
        return

    if data == "status":
        if not uid:
            return
        await q.edit_message_text(
            "🧾 <b>Статус</b>\n\n" + format_status(db, uid),
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )
        return

    await q.edit_message_text("Неизвестное действие.", reply_markup=main_menu())


def build_app() -> Application:
    cfg = load_config()
    db = Db(cfg.database_url)
    db.init_db()

    app = Application.builder().token(cfg.bot_token).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["db"] = db

    pay_service = PaymentService(
        db=db,
        mp=MercadoPagoPixProvider(access_token=cfg.mp_access_token),
    )
    app.bot_data["pay"] = pay_service

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("pay", cmd_pay))
    app.add_handler(CommandHandler("access", cmd_access))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("whoami", cmd_whoami))

    app.add_handler(CommandHandler("grant", cmd_grant))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("list_active", cmd_list_active))

    app.add_handler(CallbackQueryHandler(on_callback))
    return app


if __name__ == "__main__":
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)
