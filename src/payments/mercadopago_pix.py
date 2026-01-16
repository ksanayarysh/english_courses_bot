# payments/mercadopago_pix.py
from __future__ import annotations
import json
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .base import PixCheckout

@dataclass
class MercadoPagoPixProvider:
    access_token: str
    name: str = "mercadopago_pix"

    def create_pix_payment(self, *, amount_cents: int, description: str, payer_ref: str) -> PixCheckout:
        # MercadoPago ожидает amount в BRL как float с 2 знаками
        amount = round(amount_cents / 100.0, 2)

        url = "https://api.mercadopago.com/v1/payments"
        payload = {
            "transaction_amount": amount,
            "description": description,
            "payment_method_id": "pix",
            # payer обязателен в некоторых конфигурациях. payer_ref можно положить в external_reference.
            "external_reference": payer_ref,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            j = json.loads(body)

        external_id = str(j.get("id", ""))  # payment id in MP
        poi = (j.get("point_of_interaction") or {})
        tx = (poi.get("transaction_data") or {})

        qr_base64 = tx.get("qr_code_base64")
        copy_paste = tx.get("qr_code")

        if not external_id:
            raise RuntimeError(f"MercadoPago: no payment id. Response: {body[:500]}")

        return PixCheckout(
            provider=self.name,
            external_id=external_id,
            qr_base64=qr_base64,
            copy_paste=copy_paste,
        )
