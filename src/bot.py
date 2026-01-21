from __future__ import annotations

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from config import Config
from db import Db, now_utc
from payments.service import PaymentService
from payments.service_redirect import RedirectPaymentService


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
            [InlineKeyboardButton("💳 Перевод на карту", callback_data="pay:card")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu")],
        ]
    )



async def notify_admin(context: ContextTypes.DEFAULT_TYPE, text: str, *, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    cfg: Config = context.bot_data["cfg"]
    if not cfg.admin_chat_id:
        return
    try:
        await context.bot.send_message(chat_id=cfg.admin_chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except Exception:
        return

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
        "Клуб:\n"
        "1) Оплата PIX\n"
        "2) Доступ в приватный канал с видео\n\n"
        "Жми кнопку."
    )
    await update.effective_message.reply_text(text, reply_markup=main_menu())


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

    if data == "pay_menu":
        await q.edit_message_text("Выбери способ оплаты:", reply_markup=pay_menu())
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
        elif provider_key == "card":
            if not cfg.card_transfer_number:
                await q.edit_message_text("Способ оплаты «Перевод на карту» не настроен.", reply_markup=pay_menu())
                return
            payment_id = db.create_card_transfer_payment(
                user_id=uid,
                amount_cents=cfg.price_cents,
                currency="RUB",
                plan="manual_card",
                description="Перевод на карту (ручная проверка)",
            )
            holder = (cfg.card_transfer_holder or "").strip()
            holder_line = f"Получатель: <b>{holder}</b>\n" if holder else ""
            text = (
                "💳 <b>Перевод на карту</b>\n\n"
                f"Сумма: <b>{cfg.price_cents/100:.2f} RUB</b>\n"
                f"Платёж: <code>{payment_id}</code>\n\n"
                "Номер карты:\n"
                f"<code>{cfg.card_transfer_number}</code>\n"
                f"{holder_line}\n"
                "После перевода нажми кнопку ниже и загрузи подтверждение (скрин/чек)."
            )
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("📎 Загрузить подтверждение", callback_data=f"upload_proof:{payment_id}")],
                    [InlineKeyboardButton("⬅️ Назад", callback_data="pay_menu")],
                ]
            )
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return

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
            await notify_admin(context, f"💰 <b>Оплата подтверждена</b>\n\n👤 user_id: <code>{user_id}</code>\n🧾 payment: <code>{payment_id}</code>")

        await q.edit_message_text(
            "✅ <b>Оплата подтверждена</b>\n\nДоступ выдан на 30 дней. Жми «Уроки».",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )
        return

    if data.startswith("admin_approve:"):
        cfg = context.bot_data["cfg"]
        if not user or user.id != cfg.admin_chat_id:
            await q.answer("Not allowed", show_alert=True)
            return
        payment_id = data.split(":", 1)[1].strip()
        paid_user_id = db.mark_payment_paid(payment_id)
        if paid_user_id:
            db.set_subscription(int(paid_user_id), active=True, days=30)
            try:
                await context.bot.send_message(
                    chat_id=int(paid_user_id),
                    text="✅ Оплата подтверждена преподавателем. Доступ выдан на 30 дней. Жми «Уроки».",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_menu(),
                )
            except Exception:
                pass
        await q.edit_message_text("✅ Подтверждено. Доступ выдан.", parse_mode=ParseMode.HTML)
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

async def on_proof_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payment_id = context.user_data.get("awaiting_proof_payment_id")
    if not payment_id:
        return
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return

    proof_kind = ""
    file_id = ""
    if msg.document:
        proof_kind = "document"
        file_id = msg.document.file_id
    elif msg.photo:
        proof_kind = "photo"
        file_id = msg.photo[-1].file_id
    else:
        return

    context.user_data.pop("awaiting_proof_payment_id", None)

    db: Db = context.bot_data["db"]
    db.attach_card_transfer_proof(
        payment_id,
        proof_message_id=msg.message_id,
        proof_file_id=file_id,
        proof_kind=proof_kind,
    )

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"admin_approve:{payment_id}")]])
    await notify_admin(
        context,
        f"💳 <b>Перевод на карту</b>\n\n"
        f"👤 user_id: <code>{user.id}</code>\n"
        f"🧾 payment: <code>{payment_id}</code>\n\n"
        "Подтверждение ниже ⬇️",
        reply_markup=kb,
    )

    cfg: Config = context.bot_data["cfg"]
    if cfg.admin_chat_id:
        try:
            await context.bot.forward_message(chat_id=cfg.admin_chat_id, from_chat_id=msg.chat_id, message_id=msg.message_id)
        except Exception:
            pass

    await msg.reply_text("✅ Принято. Я передал подтверждение преподавателю.", reply_markup=main_menu())



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

    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, on_proof_message))

    app.add_handler(CallbackQueryHandler(on_callback))
    return app
