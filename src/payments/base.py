# payments/base.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Protocol

@dataclass(frozen=True)
class PixCheckout:
    provider: str
    external_id: str
    qr_base64: Optional[str]
    copy_paste: Optional[str]

class PaymentProvider(Protocol):
    name: str

    def create_pix_payment(self, *, amount_cents: int, description: str, payer_ref: str) -> PixCheckout:
        ...
