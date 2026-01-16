# payments/service.py
from __future__ import annotations
from dataclasses import dataclass

from src.db import Db
from .base import PixCheckout
from .mercadopago_pix import MercadoPagoPixProvider

@dataclass
class PaymentService:
    db: Db
    mp: MercadoPagoPixProvider

    def start_pix_checkout(self, *, user_id: int, amount_cents: int, description: str) -> str:
        payment_id = self.db.create_payment(
            user_id=user_id,
            provider=self.mp.name,
            amount_cents=amount_cents,
            currency="BRL",
        )
        checkout: PixCheckout = self.mp.create_pix_payment(
            amount_cents=amount_cents,
            description=description,
            payer_ref=f"tg:{user_id}:{payment_id}",
        )
        self.db.attach_pix_details(
            payment_id=payment_id,
            external_id=checkout.external_id,
            qr_base64=checkout.qr_base64,
            copy_paste=checkout.copy_paste,
        )
        return payment_id
