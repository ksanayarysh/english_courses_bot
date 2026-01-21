from __future__ import annotations

import secrets
import json
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List, Dict, Any

import psycopg
from psycopg.rows import dict_row

UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _new_id() -> str:
    return secrets.token_urlsafe(16)


class Db:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def init_db(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id     BIGINT PRIMARY KEY,
            status      TEXT NOT NULL,        -- active / revoked
            expires_at  TIMESTAMPTZ NULL,     -- NULL = forever
            updated_at  TIMESTAMPTZ NOT NULL
        );

        CREATE TABLE IF NOT EXISTS payments (
            id              TEXT PRIMARY KEY,     -- internal payment id
            user_id          BIGINT NOT NULL,
            provider         TEXT NOT NULL,        -- mercadopago_pix
            status           TEXT NOT NULL,        -- pending / paid / cancelled / expired
            amount_cents     BIGINT NOT NULL,
            currency         TEXT NOT NULL,        -- BRL / RUB
            external_id      TEXT NULL,            -- provider payment id
            idempotency_key  TEXT NULL,
            pay_url          TEXT NULL,            -- redirect url (e.g. YooKassa)
            raw_meta         JSONB NULL,
            pix_qr_base64    TEXT NULL,
            pix_copy_paste   TEXT NULL,
            created_at       TIMESTAMPTZ NOT NULL,
            paid_at          TIMESTAMPTZ NULL
        );

        -- Backward-compatible migrations
        ALTER TABLE payments ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
        ALTER TABLE payments ADD COLUMN IF NOT EXISTS pay_url TEXT;
        ALTER TABLE payments ADD COLUMN IF NOT EXISTS raw_meta JSONB;

        CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
        CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
        CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
        CREATE INDEX IF NOT EXISTS idx_payments_external ON payments(external_id);
        CREATE INDEX IF NOT EXISTS idx_payments_idempotency ON payments(idempotency_key);
        """
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(ddl)
            con.commit()

    # -------- Subscriptions --------
    def set_subscription(self, user_id: int, active: bool, days: Optional[int] = None) -> None:
        updated_at = now_utc()
        if not active:
            status = "revoked"
            expires_at = None
        else:
            status = "active"
            expires_at = None if days is None else (now_utc() + timedelta(days=days))

        sql = """
        INSERT INTO subscriptions (user_id, status, expires_at, updated_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            status = EXCLUDED.status,
            expires_at = EXCLUDED.expires_at,
            updated_at = EXCLUDED.updated_at;
        """
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (user_id, status, expires_at, updated_at))
            con.commit()

    def is_subscribed(self, user_id: int) -> Tuple[bool, Optional[datetime], str]:
        sql = "SELECT status, expires_at FROM subscriptions WHERE user_id=%s"
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (user_id,))
                row = cur.fetchone()

        if not row:
            return (False, None, "no_subscription")

        status = row["status"]
        expires_at = row["expires_at"]

        if status != "active":
            return (False, expires_at, "revoked")

        if expires_at and now_utc() >= expires_at:
            return (False, expires_at, "expired")

        return (True, expires_at, "active")

    def list_active(self, limit: int = 50) -> List[Dict[str, Any]]:
        sql = """
        SELECT user_id, status, expires_at, updated_at
        FROM subscriptions
        WHERE status='active'
        ORDER BY updated_at DESC
        LIMIT %s
        """
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (limit,))
                return cur.fetchall() or []

    # -------- Payments --------
    def create_payment(self, user_id: int, provider: str, amount_cents: int, currency: str = "BRL") -> str:
        pid = _new_id()
        idem = pid  # stable idempotency key for provider create calls
        sql = """
        INSERT INTO payments (
            id, user_id, provider, status, amount_cents, currency,
            external_id, idempotency_key, pay_url, raw_meta,
            pix_qr_base64, pix_copy_paste, created_at, paid_at
        )
        VALUES (
            %s, %s, %s, 'pending', %s, %s,
            NULL, %s, NULL, NULL,
            NULL, NULL, %s, NULL
        )
        """
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (pid, user_id, provider, amount_cents, currency, idem, now_utc()))
            con.commit()
        return pid


    def create_card_transfer_payment(
        self,
        user_id: int,
        amount_cents: int,
        currency: str,
        *,
        plan: str,
        description: str,
    ) -> str:
        """Creates a manual (bank card transfer) payment request."""
        payment_id = self.create_payment(user_id=user_id, provider="card_transfer", amount_cents=amount_cents, currency=currency)
        meta = {"plan": plan, "description": description}
        sql = "UPDATE payments SET raw_meta=%s WHERE id=%s"
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (json.dumps(meta), payment_id))
            con.commit()
        return payment_id

    def attach_card_transfer_proof(
        self,
        payment_id: str,
        *,
        proof_message_id: int,
        proof_file_id: str,
        proof_kind: str,
    ) -> None:
        """Stores proof and moves payment to proof_submitted."""
        sql = (
            "UPDATE payments "
            "SET status=%s, "
            "raw_meta = COALESCE(raw_meta, '{}'::jsonb) || %s::jsonb "
            "WHERE id=%s"
        )
        patch = {
            "proof_message_id": proof_message_id,
            "proof_file_id": proof_file_id,
            "proof_kind": proof_kind,
            "proof_submitted_at": now_utc().isoformat(),
        }
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, ("proof_submitted", json.dumps(patch), payment_id))
            con.commit()

    def get_payment_amount(self, payment_id: str) -> tuple[int, str]:
        p = self.get_payment(payment_id) or {}
        return int(p.get("amount_cents") or 0), str(p.get("currency") or "")

    def attach_checkout_details(self, payment_id: str, external_id: str, pay_url: Optional[str], raw_meta: Optional[dict]) -> None:
            sql = """
            UPDATE payments
            SET external_id=%s, pay_url=%s, raw_meta=%s
            WHERE id=%s
            """
            with self.connect() as con:
                with con.cursor() as cur:
                    cur.execute(sql, (external_id, pay_url, raw_meta, payment_id))
                con.commit()

    def attach_pix_details(self, payment_id: str, external_id: str, qr_base64: Optional[str], copy_paste: Optional[str]) -> None:
        sql = """
        UPDATE payments
        SET external_id=%s, pix_qr_base64=%s, pix_copy_paste=%s
        WHERE id=%s
        """
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (external_id, qr_base64, copy_paste, payment_id))
            con.commit()

    def get_payment(self, payment_id: str) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM payments WHERE id=%s"
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (payment_id,))
                return cur.fetchone()

    def find_payment_by_external_id(self, provider: str, external_id: str) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM payments WHERE provider=%s AND external_id=%s"
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (provider, external_id))
                return cur.fetchone()

    def mark_payment_paid(self, payment_id: str) -> Optional[int]:
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT user_id, status FROM payments WHERE id=%s", (payment_id,))
                row = cur.fetchone()
                if not row:
                    return None
                if row["status"] == "paid":
                    return int(row["user_id"])
                cur.execute(
                    "UPDATE payments SET status='paid', paid_at=%s WHERE id=%s",
                    (now_utc(), payment_id),
                )
            con.commit()
            return int(row["user_id"])

    def mark_payment_status(self, payment_id: str, status: str) -> None:
        sql = "UPDATE payments SET status=%s WHERE id=%s"
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (status, payment_id))
            con.commit()
