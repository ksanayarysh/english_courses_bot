from __future__ import annotations

import asyncio
import hmac
import hashlib
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from telegram import Update

from src.config import load_config
from src.db import Db
from src.payments.mercadopago_pix import MercadoPagoPixProvider
from src.payments.yookassa import YooKassaProvider
from src.payments.mock_yookassa import MockYooKassaProvider
from src.payments.service import PaymentService
from src.payments.service_redirect import RedirectPaymentService
from src.bot import build_application

from src.lessons_scheduler import lessons_scheduler_loop, send_welcome_and_lesson1


cfg = load_config()
db = Db(cfg.database_url)
db.init_db()

db.upsert_course(
    course_id=cfg.course_id,
    title=cfg.course_title,
    welcome_video_url=cfg.welcome_video_url,
    lesson_interval_days=cfg.lesson_interval_days,
)

db.add_lesson(
    course_id=cfg.course_id,
    lesson_index=1,
    title="Lesson 1: Greetings",
    video_url="https://youtu.be/FAKE_LESSON_1",
    materials_url="https://example.com/fake_lesson_1.pdf",
)

db.add_lesson(
    course_id=cfg.course_id,
    lesson_index=2,
    title="Lesson 2: Present Simple",
    video_url="https://youtu.be/FAKE_LESSON_2",
    materials_url="https://example.com/fake_lesson_2.pdf",
)

mp_provider = MercadoPagoPixProvider(access_token=cfg.mp_access_token)
pay_pix = PaymentService(db=db, provider=mp_provider)

yk_provider = YooKassaProvider(shop_id=cfg.yk_shop_id, secret_key=cfg.yk_secret_key) if (cfg.yk_shop_id and cfg.yk_secret_key) else None
mock_provider = MockYooKassaProvider()

pay_yk = RedirectPaymentService(db=db, provider=(yk_provider or mock_provider), return_url=cfg.public_base_url)
pay_mock = RedirectPaymentService(db=db, provider=mock_provider, return_url=cfg.public_base_url)

tg_app = build_application(cfg, db, pay_pix, pay_yk, pay_mock)

TG_WEBHOOK_TOKEN = (__import__("os").getenv("TG_WEBHOOK_TOKEN", "").strip() or None)
TG_SECRET_TOKEN = (__import__("os").getenv("TG_SECRET_TOKEN", "").strip() or None)


def tg_path() -> str:
    return f"/tg/{TG_WEBHOOK_TOKEN}" if TG_WEBHOOK_TOKEN else "/tg"


def _parse_x_signature(x_signature: str) -> tuple[Optional[str], Optional[str]]:
    ts = None
    v1 = None
    for part in x_signature.split(","):
        part = part.strip()
        if part.startswith("ts="):
            ts = part[3:]
        elif part.startswith("v1="):
            v1 = part[3:]
    return ts, v1


def verify_mp_signature(*, secret: str, x_signature: str, x_request_id: str, resource_id: str) -> bool:
    ts, v1 = _parse_x_signature(x_signature)
    if not ts or not v1:
        return False
    manifest = f"id:{resource_id};request-id:{x_request_id};ts:{ts};"
    digest = hmac.new(secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, v1)


app = FastAPI()
_scheduler_task: Optional[asyncio.Task] = None


@app.on_event("startup")
async def _startup() -> None:
    global _scheduler_task

    await tg_app.initialize()
    await tg_app.start()

    webhook_url = cfg.public_base_url + tg_path()
    await tg_app.bot.set_webhook(url=webhook_url, secret_token=TG_SECRET_TOKEN)

    # (7) Start background scheduler to send future lessons
    _scheduler_task = asyncio.create_task(
        lessons_scheduler_loop(bot=tg_app.bot, db=db, poll_seconds=60)
    )


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _scheduler_task
    if _scheduler_task:
        _scheduler_task.cancel()
        _scheduler_task = None
    await tg_app.stop()
    await tg_app.shutdown()


@app.get("/")
async def root():
    return {"ok": True}


@app.post("/mp/webhook")
async def mp_webhook(request: Request):
    body = await request.json()
    data = body.get("data") or {}
    external_id = str(data.get("id") or body.get("id") or "").strip()
    if not external_id:
        raise HTTPException(status_code=400, detail="No payment id")

    if cfg.mp_webhook_secret:
        x_sig = request.headers.get("x-signature")
        x_req = request.headers.get("x-request-id")
        if not x_sig or not x_req:
            raise HTTPException(status_code=401, detail="Missing signature headers")
        if not verify_mp_signature(secret=cfg.mp_webhook_secret, x_signature=x_sig, x_request_id=x_req, resource_id=external_id):
            raise HTTPException(status_code=401, detail="Invalid signature")

    p = db.find_payment_by_external_id(mp_provider.name, external_id)
    if not p:
        return {"ok": True, "ignored": True}

    status, raw = mp_provider.fetch_payment_status(external_id=external_id)
    if status == "paid":
        internal_id = p["id"]
        user_id = db.mark_payment_paid(internal_id)
        if user_id:
            db.set_subscription(int(user_id), active=True, days=30)
            await send_welcome_and_lesson1(bot=tg_app.bot, db=db, user_id=int(user_id), course_id=cfg.course_id)
        return {"ok": True, "paid": True}

    return {"ok": True, "paid": False, "status": raw}


@app.post("/yk/webhook")
async def yk_webhook(request: Request):
    body = await request.json()
    obj = body.get("object") or {}
    external_id = str(obj.get("id") or "").strip()
    if not external_id:
        raise HTTPException(status_code=400, detail="No payment id")

    provider_name = "yookassa"
    if yk_provider is None:
        return {"ok": True, "ignored": True, "reason": "yookassa not configured"}

    p = db.find_payment_by_external_id(provider_name, external_id)
    if not p:
        return {"ok": True, "ignored": True}

    status, raw = yk_provider.fetch_payment_status(external_id=external_id)
    if status == "paid":
        internal_id = p["id"]
        user_id = db.mark_payment_paid(internal_id)
        if user_id:
            db.set_subscription(int(user_id), active=True, days=30)
            await send_welcome_and_lesson1(bot=tg_app.bot, db=db, user_id=int(user_id), course_id=cfg.course_id)
        return {"ok": True, "paid": True}

    if status == "cancelled":
        db.mark_payment_status(p["id"], "cancelled")

    return {"ok": True, "paid": False, "status": raw}


@app.get("/mock/paid")
async def mock_paid(payment_id: str):
    p = db.get_payment(payment_id)
    if not p:
        raise HTTPException(status_code=404, detail="Payment not found")

    external_id = p.get("external_id")
    if external_id:
        mock_provider.mark_paid(str(external_id))

    user_id = db.mark_payment_paid(payment_id)
    if user_id:
        db.set_subscription(int(user_id), active=True, days=30)
        # (6) Immediately send welcome + lesson 1 after mock payment
        await send_welcome_and_lesson1(bot=tg_app.bot, db=db, user_id=int(user_id), course_id=cfg.course_id)

    return {"ok": True, "paid": True, "payment_id": payment_id}


@app.post("/tg")
@app.post("/tg/{token}")
async def telegram_webhook(request: Request, token: Optional[str] = None):
    if TG_WEBHOOK_TOKEN and token != TG_WEBHOOK_TOKEN:
        raise HTTPException(status_code=404, detail="Not found")

    if TG_SECRET_TOKEN:
        secret = request.headers.get("x-telegram-bot-api-secret-token")
        if secret != TG_SECRET_TOKEN:
            raise HTTPException(status_code=401, detail="Bad telegram secret")

    payload = await request.json()
    update = Update.de_json(payload, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}
