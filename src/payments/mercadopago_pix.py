from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Optional, Tuple

from .base import PixCheckout, PaymentStatus


@dataclass
class MercadoPagoPixProvider:
    access_token: str
    name: str = "mercadopago_pix"

    def _request(self, *, method: str, url: str, payload: Optional[dict] = None) -> dict:
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"MercadoPago: invalid JSON response: {body[:500]}") from e

    def create_pix_payment(self, *, amount_cents: int, description: str, payer_ref: str) -> PixCheckout:
        amount = round(amount_cents / 100.0, 2)
        j = self._request(
            method="POST",
            url="https://api.mercadopago.com/v1/payments",
            payload={
                "transaction_amount": amount,
                "description": description,
                "payment_method_id": "pix",
                "external_reference": payer_ref,
            },
        )

        external_id = str(j.get("id") or "").strip()
        poi = j.get("point_of_interaction") or {}
        tx = poi.get("transaction_data") or {}
        qr_base64 = tx.get("qr_code_base64")
        copy_paste = tx.get("qr_code")

        if not external_id:
            raise RuntimeError(f"MercadoPago: no payment id in response: {str(j)[:500]}")

        return PixCheckout(
            provider=self.name,
            external_id=external_id,
            qr_base64=qr_base64,
            copy_paste=copy_paste,
        )

    def fetch_payment_status(self, *, external_id: str) -> Tuple[PaymentStatus, Optional[str]]:
        j = self._request(
            method="GET",
            url=f"https://api.mercadopago.com/v1/payments/{external_id}",
        )
        status = (j.get("status") or "").lower()

        # У MercadoPago обычно: approved / pending / rejected / cancelled / refunded / charged_back
        if status == "approved":
            return "paid", status
        if status in ("cancelled",):
            return "cancelled", status
        if status in ("rejected", "refunded", "charged_back"):
            return "cancelled", status
        return "pending", status
