from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List, Dict, Any

import psycopg
from psycopg.rows import dict_row
import secrets
from typing import Optional, Dict, Any

def new_id() -> str:
    return secrets.token_urlsafe(16)

UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


class Db:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    # db.py (замени init_db полностью)
    def init_db(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id     BIGINT PRIMARY KEY,
            status      TEXT NOT NULL,        -- active / revoked
            expires_at  TIMESTAMPTZ NULL,     -- NULL = forever
            updated_at  TIMESTAMPTZ NOT NULL
        );

        -- Реальные платежи
        CREATE TABLE IF NOT EXISTS payments (
            id              TEXT PRIMARY KEY,     -- наш payment_id (uuid/token)
            user_id          BIGINT NOT NULL,
            provider         TEXT NOT NULL,        -- mercadopago_pix
            status           TEXT NOT NULL,        -- pending / paid / cancelled / expired
            amount_cents     BIGINT NOT NULL,
            currency         TEXT NOT NULL,        -- BRL
            external_id      TEXT NULL,            -- id платежа у провайдера
            pix_qr_base64    TEXT NULL,            -- QR картинка (base64)
            pix_copy_paste   TEXT NULL,            -- PIX "copia e cola"
            created_at       TIMESTAMPTZ NOT NULL,
            paid_at          TIMESTAMPTZ NULL
        );

        CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
        CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
        CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
        CREATE INDEX IF NOT EXISTS idx_payments_external ON payments(external_id);
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

    # -------- Fake payments (demo) --------
    def create_fake_payment(self, user_id: int) -> str:
        payment_id = secrets.token_urlsafe(10)
        sql = """
        INSERT INTO fake_payments (payment_id, user_id, status, created_at, paid_at)
        VALUES (%s, %s, 'pending', %s, NULL)
        """
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (payment_id, user_id, now_utc()))
            con.commit()
        return payment_id

    def mark_fake_payment_paid(self, payment_id: str) -> Optional[int]:
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT user_id, status FROM fake_payments WHERE payment_id=%s", (payment_id,))
                row = cur.fetchone()
                if not row:
                    return None
                if row["status"] == "paid":
                    return int(row["user_id"])

                cur.execute(
                    "UPDATE fake_payments SET status='paid', paid_at=%s WHERE payment_id=%s",
                    (now_utc(), payment_id),
                )
            con.commit()
            return int(row["user_id"])


    def create_payment(
            self,
            user_id: int,
            provider: str,
            amount_cents: int,
            currency: str = "BRL",
        ) -> str:
            pid = new_id()
            sql = """
            INSERT INTO payments (id, user_id, provider, status, amount_cents, currency, external_id, pix_qr_base64, pix_copy_paste, created_at, paid_at)
            VALUES (%s, %s, %s, 'pending', %s, %s, NULL, NULL, NULL, %s, NULL)
            """
            with self.connect() as con:
                with con.cursor() as cur:
                    cur.execute(sql, (pid, user_id, provider, amount_cents, currency, now_utc()))
                con.commit()
            return pid

    def attach_pix_details(
                self,
                payment_id: str,
                external_id: str,
                qr_base64: Optional[str],
                copy_paste: Optional[str],
        ) -> None:
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

        def find_payment_by_external_id(self, provider: str, external_id: str) -> Optional[Dict[str, Any]]:
            sql = "SELECT * FROM payments WHERE provider=%s AND external_id=%s"
            with self.connect() as con:
                with con.cursor() as cur:
                    cur.execute(sql, (provider, external_id))
                    return cur.fetchone()

