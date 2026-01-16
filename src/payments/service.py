from __future__ import annotations

from dataclasses import dataclass

from src.db import Db
from .base import PaymentProvider


@dataclass
class PaymentService:
    db: Db
    provider: PaymentProvider

    def start_pix_checkout(self, *, user_id: int, amount_cents: int, description: str) -> str:
        payment_id = self.db.create_payment(
            user_id=user_id,
            provider=self.provider.name,
            amount_cents=amount_cents,
            currency="BRL",
        )

        # Use internal payment_id as a stable idempotency key
        idempotency_key = payment_id

        checkout = self.provider.create_pix_payment(
            amount_cents=amount_cents,
            description=description,
            payer_ref=f"tg:{user_id}:{payment_id}",
            idempotency_key=idempotency_key,
        )
        self.db.attach_pix_details(
            payment_id=payment_id,
            external_id=checkout.external_id,
            qr_base64=checkout.qr_base64,
            copy_paste=checkout.copy_paste,
        )
        return payment_id

    def refresh_and_mark_paid_if_needed(self, *, payment_id: str) -> bool:
        p = self.db.get_payment(payment_id)
        if not p:
            return False
        if p["status"] == "paid":
            return True
        external_id = p.get("external_id")
        if not external_id:
            return False
        status, raw = self.provider.fetch_payment_status(external_id=external_id)
        if status == "paid":
            self.db.mark_payment_paid(payment_id)
            return True
        return False
