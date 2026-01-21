from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .yookassa import RedirectCheckout, YkPaymentStatus


@dataclass
class MockYooKassaProvider:
    """A no-network provider to test the 'redirect + webhook' flow.

    It deterministically maps idempotency_key -> external_id, so repeated
    'create' calls don't create duplicates.
    """

    name: str = "mock"
    _state: dict[str, YkPaymentStatus] = None

    def __post_init__(self) -> None:
        if self._state is None:
            self._state = {}

    def create_payment(
        self,
        *,
        amount_cents: int,
        description: str,
        payer_ref: str,
        idempotency_key: str,
        return_url: str,
    ) -> RedirectCheckout:
        external_id = f"mock_{idempotency_key}"
        self._state.setdefault(external_id, "pending")
        pay_url = f"{return_url.rstrip('/')}?mock=1&external_id={external_id}"
        return RedirectCheckout(provider=self.name, external_id=external_id, pay_url=pay_url, raw_meta={"payer_ref": payer_ref})

    def fetch_payment_status(self, *, external_id: str) -> Tuple[YkPaymentStatus, Optional[str]]:
        return self._state.get(external_id, "pending"), None

    def mark_paid(self, external_id: str) -> None:
        self._state[external_id] = "paid"

    def mark_cancelled(self, external_id: str) -> None:
        self._state[external_id] = "cancelled"