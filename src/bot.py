from __future__ import annotations

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from src.config import Config
from src.db import Db, now_utc
from src.payments.service import PaymentService
from src.payments.service_redirect import RedirectPaymentService


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💳 Оплата", callback_data="pay_menu")],
            [InlineKeyboardButton("🎬 Уроки (после оплаты)", callback_data="access")],
            [InlineKeyboardButton("🧾 Статус", callback_data="status")],
        ]
    )


def pay_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🇧🇷 PIX (Brasil)", callback_data="pay:pix")],
            [InlineKeyboardButton("🇷🇺 Карта / СБП (YooKassa)", callback_data="pay:yookassa")],
            [InlineKeyboardButton("🧪 Тестовая оплата (Mock)", callback_data="pay:mock")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu")],
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
    name = user.first_name if user else "Незнакомец"
    await update.message.reply_text(
        f"Привет, {name}! Я очень рада тебя тут видеть! Выбери формат обучения:",
        reply_markup=format_menu()
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Db = context.bot_data["db"]
    user = update.effective_user
    if not user:
        return
    text = "🧾 <b>Статус</b>\n\n" + format_status(db, user.id)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu())


async def cmd_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["cfg"]
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


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["cfg"]
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
    cfg: Config = context.bot_data["cfg"]
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
    await update.effective_message.reply_text(
        f"✅ Выдан доступ {uid} " + (f"на {days} дней." if days else "без срока.")
    )


async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["cfg"]
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
    cfg: Config = context.bot_data["cfg"]
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
    cfg: Config = context.bot_data["cfg"]
    db: Db = context.bot_data["db"]
    pay_pix: PaymentService = context.bot_data["pay_pix"]
    pay_yk: RedirectPaymentService = context.bot_data["pay_yookassa"]
    pay_mock: RedirectPaymentService = context.bot_data["pay_mock"]

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

    if data.startswith("plan:"):
        if not uid:
            return

        plan = data.split(":", 1)[1]
        db.set_user_plan(user_id=uid, plan=plan)

        if plan == "mixed":
            price = cfg.price_mixed_cents
            desc = "Видео + практика (8 занятий / месяц)"
        else:
            price = cfg.price_live_cents
            desc = "Все занятия вживую (8 занятий / месяц)"

        await q.edit_message_text(
            f"Формат выбран:\n<b>{desc}</b>\n\n"
            f"Стоимость: <b>{price / 100:.2f}</b>\n\n"
            "Нажми «Оплата».",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Оплата", callback_data="pay_menu")],
                [InlineKeyboardButton("🔁 Изменить формат", callback_data="format_menu")],
            ])
        )
        return

    if data == "pay_menu":
        await q.edit_message_text("Выбери способ оплаты:", reply_markup=pay_menu())
        return

    if data == "format_menu":
        await q.edit_message_text(
            "Выбери формат обучения:",
            reply_markup=format_menu()
        )
        return

    if data.startswith("pay:"):
        if not uid:
            return
        provider_key = data.split(":", 1)[1].strip()

        if provider_key == "pix":
            payment_id = pay_pix.start_pix_checkout(
                user_id=uid,
                amount_cents=cfg.price_cents,
                description="Доступ в приватный канал с уроками (30 дней)",
            )
            p = db.get_payment(payment_id) or {}
            copy_paste = p.get("pix_copy_paste")
            text = (
                "💳 <b>Оплата PIX</b>\n\n"
                f"Сумма: <b>{cfg.price_cents/100:.2f} BRL</b>\n"
                f"Платёж: <code>{payment_id}</code>\n\n"
                "1) Открой банк\n2) PIX → Copia e Cola\n3) Вставь код ниже\n\n"
                f"<code>{copy_paste or 'PIX-код не получен, см. логи'}</code>\n\n"
                "После оплаты нажми «Проверить оплату»."
            )
        elif provider_key == "yookassa":
            payment_id = pay_yk.start_checkout(
                user_id=uid,
                amount_cents=cfg.price_cents,
                description="Доступ в приватный канал с уроками (30 дней)",
            )
            p = db.get_payment(payment_id) or {}
            pay_url = p.get("pay_url")
            text = (
                "💳 <b>Оплата (YooKassa)</b>\n\n"
                f"Сумма: <b>{cfg.price_cents/100:.2f} RUB</b>\n"
                f"Платёж: <code>{payment_id}</code>\n\n"
                "Перейди по ссылке для оплаты:\n"
                f"{pay_url or '(ссылка не получена, см. логи)'}\n\n"
                "После оплаты вернись и нажми «Проверить оплату»."
            )
        elif provider_key == "mock":
            payment_id = pay_mock.start_checkout(
                user_id=uid,
                amount_cents=cfg.price_cents,
                description="TEST: Доступ в приватный канал (30 дней)",
            )
            p = db.get_payment(payment_id) or {}
            pay_url = p.get("pay_url")
            text = (
                "🧪 <b>Тестовая оплата (мок)</b>\n\n"
                f"Платёж: <code>{payment_id}</code>\n\n"
                "Это не настоящая оплата.\n"
                "Чтобы 'оплатить', открой:\n"
                f"{cfg.public_base_url}/mock/paid?payment_id={payment_id}\n\n"
                "И потом нажми «Проверить оплату» в боте.\n\n"
                f"Ссылка (для вида): {pay_url or ''}"
            )
        else:
            await q.edit_message_text("Неизвестный способ оплаты.", reply_markup=pay_menu())
            return

        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_payment:{payment_id}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu")],
            ]
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("check_payment:"):
        payment_id = data.split(":", 1)[1].strip()
        p = db.get_payment(payment_id)
        if not p:
            await q.edit_message_text("Платёж не найден.", reply_markup=main_menu())
            return

        provider = (p.get("provider") or "").lower()
        if provider == "mercadopago_pix":
            ok = pay_pix.refresh_and_mark_paid_if_needed(payment_id=payment_id)
        elif provider == "yookassa":
            ok = pay_yk.refresh_and_mark_paid_if_needed(payment_id=payment_id)
        elif provider == "mock_yookassa":
            ok = pay_mock.refresh_and_mark_paid_if_needed(payment_id=payment_id)
        else:
            ok = False
        if not ok:
            await q.edit_message_text(
                "⏳ Оплата пока не подтверждена.\n"
                "Если оплатил(а) только что, подожди минуту и нажми ещё раз.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_payment:{payment_id}")],
                        [InlineKeyboardButton("⬅️ Назад", callback_data="menu")],
                    ]
                ),
            )
            return

        p = db.get_payment(payment_id) or {}
        user_id = int(p.get("user_id", uid or 0) or 0)
        if user_id:
            db.set_subscription(user_id, active=True, days=30)

        await q.edit_message_text(
            "✅ <b>Оплата подтверждена</b>\n\nДоступ выдан на 30 дней. Жми «Уроки».",
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


def build_application(
    cfg: Config,
    db: Db,
    pay_pix: PaymentService,
    pay_yookassa: RedirectPaymentService,
    pay_mock: RedirectPaymentService,
) -> Application:
    app = Application.builder().token(cfg.bot_token).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["db"] = db
    app.bot_data["pay_pix"] = pay_pix
    app.bot_data["pay_yookassa"] = pay_yookassa
    app.bot_data["pay_mock"] = pay_mock

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("access", cmd_access))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("whoami", cmd_whoami))

    app.add_handler(CommandHandler("grant", cmd_grant))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("list_active", cmd_list_active))

    app.add_handler(CallbackQueryHandler(on_callback))
    return app

def format_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 Видео + практика (8 занятий)", callback_data="plan:mixed")],
        [InlineKeyboardButton("🧑‍🏫 Все занятия вживую (8 занятий)", callback_data="plan:live")],
    ])
