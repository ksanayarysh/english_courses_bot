from __future__ import annotations

import hmac
import hashlib
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from telegram import Update

from src.config import load_config
from src.db import Db
from src.payments.mercadopago_pix import MercadoPagoPixProvider
from src.payments.service import PaymentService
from src.bot import build_application


cfg = load_config()
db = Db(cfg.database_url)
db.init_db()

provider = MercadoPagoPixProvider(access_token=cfg.mp_access_token)
pay = PaymentService(db=db, provider=provider)

tg_app = build_application(cfg, db, pay)

TG_WEBHOOK_TOKEN = ( __import__("os").getenv("TG_WEBHOOK_TOKEN", "").strip() or None )
TG_SECRET_TOKEN = ( __import__("os").getenv("TG_SECRET_TOKEN", "").strip() or None )


def tg_path() -> str:
    return f"/tg/{TG_WEBHOOK_TOKEN}" if TG_WEBHOOK_TOKEN else "/tg"


def _parse_x_signature(x_signature: str) -> tuple[Optional[str], Optional[str]]:
    # expected format like: "ts=1700000000,v1=abcdef..."
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


@app.on_event("startup")
async def _startup() -> None:
    await tg_app.initialize()
    await tg_app.start()

    # Telegram requires HTTPS public URL
    webhook_url = cfg.public_base_url + tg_path()
    await tg_app.bot.set_webhook(url=webhook_url, secret_token=TG_SECRET_TOKEN)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await tg_app.stop()
    await tg_app.shutdown()


@app.get("/")
async def root():
    return {"ok": True}


@app.post("/mp/webhook")
async def mp_webhook(request: Request):
    body = await request.json()

    # MercadoPago typically sends: {"type":"payment", "data": {"id": "123"}, ...}
    data = body.get("data") or {}
    external_id = str(data.get("id") or body.get("id") or "").strip()
    if not external_id:
        raise HTTPException(status_code=400, detail="No payment id")

    # optional signature verification
    if cfg.mp_webhook_secret:
        x_sig = request.headers.get("x-signature")
        x_req = request.headers.get("x-request-id")
        if not x_sig or not x_req:
            raise HTTPException(status_code=401, detail="Missing signature headers")
        if not verify_mp_signature(secret=cfg.mp_webhook_secret, x_signature=x_sig, x_request_id=x_req, resource_id=external_id):
            raise HTTPException(status_code=401, detail="Invalid signature")

    # Map MP payment id -> our internal payment
    p = db.find_payment_by_external_id(provider.name, external_id)
    if not p:
        # ignore unknown payments (e.g., wrong env)
        return {"ok": True, "ignored": True}

    # Trust but verify: query MP API for status
    status, raw = provider.fetch_payment_status(external_id=external_id)
    if status == "paid":
        internal_id = p["id"]
        user_id = db.mark_payment_paid(internal_id)
        if user_id:
            db.set_subscription(int(user_id), active=True, days=30)
        return {"ok": True, "paid": True}

    return {"ok": True, "paid": False, "status": raw}


@app.post("/tg")
@app.post("/tg/{token}")
async def telegram_webhook(request: Request, token: Optional[str] = None):
    # If token is configured, path must match
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
