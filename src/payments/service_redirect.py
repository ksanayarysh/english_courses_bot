from __future__ import annotations

from dataclasses import dataclass

from db import Db


@dataclass
class RedirectPaymentService:
    """Service for redirect-based payments (e.g. YooKassa).

    Works similarly to PaymentService for PIX, but stores a payment URL.
    """

    db: Db
    provider: object
    return_url: str

    def start_checkout(self, *, user_id: int, amount_cents: int, description: str) -> str:
        payment_id = self.db.create_payment(
            user_id=user_id,
            provider=self.provider.name,
            amount_cents=amount_cents,
            currency="RUB",
        )

        checkout = self.provider.create_payment(
            amount_cents=amount_cents,
            description=description,
            payer_ref=f"tg:{user_id}:{payment_id}",
            idempotency_key=payment_id,
            return_url=self.return_url,
        )

        self.db.attach_checkout_details(
            payment_id=payment_id,
            external_id=checkout.external_id,
            pay_url=checkout.pay_url,
            raw_meta=checkout.raw_meta,
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
        if status == "cancelled":
            self.db.mark_payment_status(payment_id, "cancelled")
        return False
