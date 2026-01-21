from __future__ import annotations

from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.config import Config, PRICES
from src.db import Db
from src.payments.utils import get_currency_by_provider
from src.plans import Plan, get_plan_label
from src.payments.service import PaymentService
from src.payments.service_redirect import RedirectPaymentService

def format_prices(plan: str) -> str:
    brl = PRICES[plan]["BRL"] / 100
    rub = PRICES[plan]["RUB"] / 100

    return (
        f"💰 Стоимость:\n"
        f"• <b>{brl:,.2f} BRL</b>\n"
        f"• <b>{rub:,.0f} ₽</b>"
    )


def _main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💳 Оплата", callback_data="pay_menu")],
            [InlineKeyboardButton("🧾 Статус", callback_data="status_menu")],
        ]
    )


def _plans_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🧑‍🏫 Все занятия online", callback_data="plan:live_only")],
            [InlineKeyboardButton("🎥 Online + видео", callback_data="plan:mixed")],
        ]
    )


def _pay_methods_menu(cfg: Config) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🇧🇷 PIX (Brasil)", callback_data="pay:pix")],
        [InlineKeyboardButton("🇷🇺 Карта / СБП (YooKassa)", callback_data="pay:yookassa")],
        [InlineKeyboardButton("🧪 Тестовая оплата (Mock)", callback_data="pay:mock")],
    ]
    # manual card transfer is optional
    if cfg.card_transfer_number:
        rows.append([InlineKeyboardButton("💳 Перевод на карту", callback_data="pay:card_transfer")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back:main")])
    return InlineKeyboardMarkup(rows)


async def _notify_admin(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    cfg: Config = context.bot_data["cfg"]
    if not cfg.admin_chat_id:
        return
    try:
        await context.bot.send_message(chat_id=cfg.admin_chat_id, text=text, parse_mode=ParseMode.HTML)
    except Exception:
        # don't crash user flow because admin notifications failed
        return


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["cfg"]
    db: Db = context.bot_data["db"]
    user = update.effective_user

    if not user:
        return

    # Ensure we have a plan (default mixed)
    plan = db.get_user_plan(user_id=user.id)
    if plan is None:
        db.set_user_plan(user_id=user.id, plan=Plan.MIXED.value)
        plan = Plan.MIXED.value

    await update.effective_message.reply_text(
        "Привет! Я очень рада вас тут видеть! 👋\n\nВыберите формат обучения:",
        reply_markup=_plans_menu(),
    )


async def _show_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["cfg"]
    db: Db = context.bot_data["db"]
    user = update.effective_user
    if not user:
        return
    plan = db.get_user_plan(user_id=user.id) or Plan.MIXED.value
    amount = cfg.price_for_plan_currency_cents(plan, "BRL")

    text = (
        f"Привет, {user.first_name or 'друг'}!\n\n"
        f"Формат: <b>{get_plan_label(plan)}</b>\n"
        f"Оплата за месяц: <b>{amount/100:.2f} {cfg.currency}</b>\n\n"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=_main_menu())


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()

    cfg: Config = context.bot_data["cfg"]
    db: Db = context.bot_data["db"]
    pay: PaymentService = context.bot_data["pay"]
    pay_yk: RedirectPaymentService = context.bot_data.get("pay_yookassa")  # optional
    pay_mock: RedirectPaymentService = context.bot_data.get("pay_mock")    # optional

    user = q.from_user
    if not user:
        return
    uid = user.id
    data = q.data or ""

    if data.startswith("plan:"):
        plan = data.split(":", 1)[1]
        if plan not in (Plan.LIVE_ONLY.value, Plan.MIXED.value):
            plan = Plan.MIXED.value
        db.set_user_plan(user_id=uid, plan=plan)
        await q.edit_message_text(
            f"Ок, выбран формат: <b>{get_plan_label(plan)}</b>\n\nТеперь главное меню:",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_menu(),
        )
        return

    if data == "pay_menu":
        plan = db.get_user_plan(user_id=uid) or Plan.MIXED.value

        # две валюты, 4 цены (по плану) должны лежать в env и читаться cfg:
        # PRICE_LIVE_ONLY_BRL / PRICE_LIVE_ONLY_RUB / PRICE_MIXED_BRL / PRICE_MIXED_RUB
        brl_cents = cfg.price_for_plan_currency_cents(plan, "BRL")
        rub_cents = cfg.price_for_plan_currency_cents(plan, "RUB")

        await q.edit_message_text(
            text=(
                "Выбери способ оплаты.\n"
                f"Формат: <b>{get_plan_label(plan)}</b>\n"
                "Стоимость:\n"
                f"• <b>{brl_cents / 100:.2f} BRL</b>\n"
                f"• <b>{rub_cents / 100:.0f} ₽</b>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=_pay_methods_menu(cfg),
        )
        return

    if data.startswith("pay:"):
        provider_key = data.split(":", 1)[1]
        plan = db.get_user_plan(user_id=uid) or Plan.MIXED.value

        # single source of truth for currency/amount
        currency = get_currency_by_provider(provider_key)
        amount_cents = PRICES[plan][currency]

        payment_id = db.create_payment(
            user_id=uid,
            provider=provider_key,
            amount_cents=amount_cents,
            currency=currency,  # <-- FIX: was cfg.currency
            plan=plan,
        )

        if provider_key == "pix":
            try:
                await q.edit_message_text("⏳ Создаю PIX…", reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Назад", callback_data="pay_menu")]]
                ))
                checkout = pay.start_pix_checkout(
                    user_id=uid,
                    amount_cents=amount_cents,
                    currency=currency,
                    plan=plan,
                    description=cfg.payment_description(plan),
                )

                db.attach_pix_details(
                    payment_id=checkout.payment_id,
                    external_id=checkout.external_id,
                    qr_base64=checkout.qr_base64,
                    copy_paste=checkout.copy_paste,
                )
                code = checkout.copy_paste or "(код не получен)"
                await q.edit_message_text(
                    (
                        "💳 <b>Оплата PIX</b>\n\n"
                        f"Сумма: <b>{amount_cents / 100:.2f} {currency}</b>\n"
                        f"Платёж: <code>{checkout.payment_id}</code>\n\n"
                        "<b>PIX Copia e Cola:</b>\n"
                        f"<code>{code}</code>\n\n"
                        "Откройте приложение банка → PIX → Copia e Cola и вставь код из сообщения.\n"
                        "После оплаты нажмите «Проверить оплату»."
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check:{payment_id}")],
                            [InlineKeyboardButton("⬅️ Назад", callback_data="pay_menu")],
                        ]
                    ),
                )
            except Exception as e:
                logger.exception("PIX checkout failed")
                await q.edit_message_text(
                    "⚠️ PIX сейчас не получается создать. Попробуйте позже или выберите другой способ оплаты.",
                    reply_markup=_pay_methods_menu(cfg),
                )
            return

        if provider_key == "yookassa":
            if not pay_yk:
                await q.edit_message_text("YooKassa не настроена.", reply_markup=_pay_methods_menu(cfg))
                return

            # IMPORTANT: сервис сам создаёт payment + attach_checkout_details
            payment_id = pay_yk.start_checkout(
                user_id=uid,
                amount_cents=amount_cents,
                description=cfg.payment_description(plan),
                plan=plan,
                currency=currency,
            )

            p = db.get_payment(payment_id) or {}
            pay_url = p.get("pay_url")
            currency_db = p.get("currency") or currency  # на случай если сервис жёстко шьёт RUB

            await q.edit_message_text(
                (
                    "💳 <b>Карта / СБП (YooKassa)</b>\n\n"
                    f"Сумма: <b>{amount_cents / 100:.2f} {currency_db}</b>\n"
                    f"Платёж: <code>{payment_id}</code>\n\n"
                    f"Ссылка на оплату:\n{pay_url or '(ссылка не найдена)'}\n\n"
                    "После оплаты вернитесь и нажмите «Проверить оплату»."
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check:{payment_id}")],
                        [InlineKeyboardButton("⬅️ Назад", callback_data="pay_menu")],
                    ]
                ),
            )
            return

        if provider_key == "mock":
            if not pay_mock:
                await q.edit_message_text("Mock не настроен.", reply_markup=_pay_methods_menu(cfg))
                return

            await q.edit_message_text("⏳ Создаю mock…", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Назад", callback_data="pay_menu")]]
            ))

            # IMPORTANT: pay_mock.start_checkout сам создаёт payment и возвращает payment_id
            payment_id = pay_mock.start_checkout(
                user_id=uid,
                amount_cents=amount_cents,
                description="TEST: " + cfg.payment_description(plan),
                plan=plan,
                currency=currency,
            )

            # Достаём pay_url из БД (сервис уже сделал attach_checkout_details)
            p = db.get_payment(payment_id)
            pay_url = (p or {}).get("pay_url")

            await q.edit_message_text(
                (
                    "🧪 <b>Тестовая оплата (mock)</b>\n\n"
                    f"Платёж: <code>{payment_id}</code>\n\n"
                    f"Открой ссылку и отметь как оплачено:\n{pay_url or '(ссылка не найдена)'}\n\n"
                    "Затем нажми «Проверить оплату»."
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check:{payment_id}")],
                        [InlineKeyboardButton("⬅️ Назад", callback_data="pay_menu")],
                    ]
                ),
            )
            return

        if provider_key == "card_transfer":
            if not cfg.card_transfer_number:
                await q.edit_message_text("Перевод на карту не настроен.", reply_markup=_pay_methods_menu(cfg))
                return
            holder = (cfg.card_transfer_holder or "").strip()
            holder_line = f"\nПолучатель: <b>{holder}</b>" if holder else ""
            await q.edit_message_text(
                (
                    "💳 <b>Перевод на карту</b>\n\n"
                    f"Сумма: <b>{amount_cents / 100:.2f} {currency}</b>\n"  # <-- FIX
                    f"Карта: <code>{cfg.card_transfer_number}</code>{holder_line}\n\n"
                    "После перевода загрузите подтверждение (скрин/чек)."
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("📎 Загрузить подтверждение", callback_data=f"upload_proof:{payment_id}")],
                        [InlineKeyboardButton("⬅️ Назад", callback_data="pay_menu")],
                    ]
                ),
            )
            return

        await q.edit_message_text("Неизвестный способ оплаты.", reply_markup=_pay_methods_menu(cfg))
        return

    if data.startswith("upload_proof:"):
        payment_id = data.split(":", 1)[1]
        context.user_data["awaiting_proof_payment_id"] = payment_id
        await q.edit_message_text(
            "Пришлите сюда подтверждение оплаты (фото или файл).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="pay_menu")]]),
        )
        return

    if data.startswith("approve_manual:"):
        # Admin approves manual transfer
        payment_id = data.split(":", 1)[1]
        if uid != cfg.admin_chat_id:
            await q.edit_message_text("Нет прав.")
            return
        p = db.get_payment(payment_id)
        if not p:
            await q.edit_message_text("Платёж не найден.")
            return
        if p["status"] != "paid":
            db.mark_payment_paid(payment_id)

        await _on_payment_paid(context, payment_id, manual=True)
        await q.edit_message_text("✅ Оплата подтверждена. Доступ выдан.")
        return

    if data.startswith("check:"):
        payment_id = data.split(":", 1)[1]
        p = db.get_payment(payment_id)
        if not p:
            await q.edit_message_text("Платёж не найден.", reply_markup=_main_menu())
            return

        paid = pay.refresh_and_mark_paid_if_needed(payment_id=payment_id)

        if paid:
            await q.edit_message_text(
                "✅ Оплата подтверждена! Доступ активирован."
            )
        else:
            await q.answer("Пока не вижу оплату. Попробуй чуть позже.", show_alert=False)
        return

    if data == "back:main":
        await _show_main(update, context)
        return


async def on_proof_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["cfg"]
    db: Db = context.bot_data["db"]

    payment_id = context.user_data.get("awaiting_proof_payment_id")
    if not payment_id:
        return

    # We accept a document or a photo
    msg = update.effective_message
    if not msg:
        return
    if not (msg.document or msg.photo):
        return

    # stop awaiting
    context.user_data["awaiting_proof_payment_id"] = None

    p = db.get_payment(payment_id) or {}
    user_id = p.get("user_id")
    plan = p.get("plan") or db.get_user_plan(user_id=user_id) or Plan.MIXED.value

    await _notify_admin(
        context,
        f"💳 <b>Перевод на карту</b>\n\n"
        f"👤 user_id: <code>{user_id}</code>\n"
        f"📦 формат: <b>{get_plan_label(plan)}</b>\n"
        f"🆔 payment_id: <code>{payment_id}</code>\n\n"
        "Подтверждение ниже ⬇️",
    )

    if cfg.admin_chat_id:
        await context.bot.forward_message(
            chat_id=cfg.admin_chat_id,
            from_chat_id=update.effective_chat.id,
            message_id=msg.message_id,
        )
        await context.bot.send_message(
            chat_id=cfg.admin_chat_id,
            text="Подтвердить оплату?",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"approve_manual:{payment_id}")]]
            ),
        )

    await update.effective_message.reply_text("✅ Принято! Я передала подтверждение, ожидай подтверждения.", reply_markup=_main_menu())


async def _on_payment_paid(context: ContextTypes.DEFAULT_TYPE, payment_id: str, manual: bool = False) -> None:
    """Single place for post-payment side effects (user + admin notifications, access, scheduling)."""
    cfg: Config = context.bot_data["cfg"]
    db: Db = context.bot_data["db"]

    p = db.get_payment(payment_id) or {}
    user_id = int(p.get("user_id"))
    plan = (p.get("plan") or db.get_user_plan(user_id=user_id) or Plan.MIXED.value)

    # subscription activation
    db.set_subscription(user_id, active=True, days=cfg.subscription_days)

    # notify admin
    await _notify_admin(
        context,
        f"💰 <b>Новая оплата</b>\n\n"
        f"👤 user_id: <code>{user_id}</code>\n"
        f"📦 формат: <b>{get_plan_label(plan)}</b>\n"
        f"💳 способ: <code>{p.get('provider')}</code>\n"
        f"🆔 payment_id: <code>{payment_id}</code>\n"
        f"✅ статус: <b>paid</b>",
    )

    # send user welcome payload
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ Оплата получена! Добро пожаловать 👋",
        )
    except Exception:
        pass

    # Plan-specific behavior:
    if plan == Plan.MIXED.value:
        # Initialize lessons schedule if available (optional module)
        try:
            from src.lessons_scheduler import init_user_lessons_progress  # type: ignore

            init_user_lessons_progress(db, user_id=user_id, course_id=cfg.course_id, start_at=None)
        except Exception:
            # lessons module may not exist in some deployments
            pass

    # For live_only: do nothing automatic with lessons

import logging
logger = logging.getLogger(__name__)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)

def build_application(cfg: Config, db: Db, pay: PaymentService, pay_yookassa: Optional[RedirectPaymentService] = None, pay_mock: Optional[RedirectPaymentService] = None) -> Application:
    app = Application.builder().token(cfg.bot_token).build()

    app.bot_data["cfg"] = cfg
    app.bot_data["db"] = db
    app.bot_data["pay"] = pay
    if pay_yookassa:
        app.bot_data["pay_yookassa"] = pay_yookassa
    if pay_mock:
        app.bot_data["pay_mock"] = pay_mock

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, on_proof_message))
    app.add_error_handler(on_error)
    return app
