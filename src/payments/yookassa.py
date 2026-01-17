from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, Tuple, Literal


YkPaymentStatus = Literal["pending", "paid", "cancelled"]


@dataclass(frozen=True)
class RedirectCheckout:
    provider: str
    external_id: str
    pay_url: Optional[str]
    raw_meta: Optional[dict]


@dataclass
class YooKassaProvider:
    shop_id: str
    secret_key: str
    api_base: str = "https://api.yookassa.ru/v3"
    name: str = "yookassa"

    def _auth_header(self) -> str:
        token = f"{self.shop_id}:{self.secret_key}".encode("utf-8")
        return "Basic " + base64.b64encode(token).decode("ascii")

    def _request(
        self,
        *,
        method: str,
        path: str,
        payload: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        url = self.api_base + path
        data = None if payload is None else json.dumps(payload).encode("utf-8")

        headers = {
            "Authorization": self._auth_header(),
            "Content-Type": "application/json",
        }
        if idempotency_key is not None:
            k = str(idempotency_key).strip()
            if not k:
                raise ValueError("idempotency_key cannot be empty")
            # YooKassa expects this exact header name
            headers["Idempotence-Key"] = k

        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"YooKassa error {e.code}: {err}")

        return json.loads(body)

    def create_payment(
        self,
        *,
        amount_cents: int,
        description: str,
        payer_ref: str,
        idempotency_key: str,
        return_url: str,
    ) -> RedirectCheckout:
        amount_value = f"{amount_cents / 100:.2f}"
        j = self._request(
            method="POST",
            path="/payments",
            idempotency_key=idempotency_key,
            payload={
                "amount": {"value": amount_value, "currency": "RUB"},
                "capture": True,
                "description": description,
                "confirmation": {"type": "redirect", "return_url": return_url},
                "metadata": {"payer_ref": payer_ref},
            },
        )

        external_id = str(j.get("id") or "").strip()
        conf = j.get("confirmation") or {}
        pay_url = conf.get("confirmation_url")
        if not external_id:
            raise RuntimeError(f"YooKassa: no payment id in response: {str(j)[:500]}")

        return RedirectCheckout(provider=self.name, external_id=external_id, pay_url=pay_url, raw_meta=j)

    def fetch_payment_status(self, *, external_id: str) -> Tuple[YkPaymentStatus, Optional[str]]:
        j = self._request(method="GET", path=f"/payments/{external_id}")
        status = (j.get("status") or "").lower()
        if status == "succeeded":
            return "paid", status
        if status == "canceled":
            return "cancelled", status
        return "pending", status
