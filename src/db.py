from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List, Dict, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

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
            provider         TEXT NOT NULL,        -- mercadopago_pix / yookassa / mockpay
            status           TEXT NOT NULL,        -- pending / paid / cancelled / expired
            amount_cents     BIGINT NOT NULL,
            currency         TEXT NOT NULL,        -- BRL / RUB
            plan             TEXT NULL,
            external_id      TEXT NULL,            -- provider payment id
            idempotency_key  TEXT NULL,            -- for provider create calls
            pay_url          TEXT NULL,            -- redirect URL (YooKassa)
            raw_meta         JSONB NULL,           -- raw provider response (optional)
            pix_qr_base64    TEXT NULL,
            pix_copy_paste   TEXT NULL,
            created_at       TIMESTAMPTZ NOT NULL,
            paid_at          TIMESTAMPTZ NULL
        );

        -- Backward-compatible migrations
        ALTER TABLE payments ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
        ALTER TABLE payments ADD COLUMN IF NOT EXISTS pay_url TEXT;
        ALTER TABLE payments ADD COLUMN IF NOT EXISTS raw_meta JSONB;
        ALTER TABLE payments ADD COLUMN IF NOT EXISTS external_id TEXT;
        ALTER TABLE payments ADD COLUMN IF NOT EXISTS provider TEXT;
        ALTER TABLE payments ADD COLUMN IF NOT EXISTS plan TEXT;  -- <-- ADD THIS

        CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
        CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
        CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
        CREATE INDEX IF NOT EXISTS idx_payments_external ON payments(external_id);
        CREATE INDEX IF NOT EXISTS idx_payments_provider ON payments(provider);
        CREATE INDEX IF NOT EXISTS idx_payments_idempotency ON payments(idempotency_key);

        CREATE TABLE IF NOT EXISTS courses (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              welcome_video_url TEXT NULL,
              lesson_interval_days INT NOT NULL DEFAULT 7,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        
            CREATE TABLE IF NOT EXISTS lessons (
              id SERIAL PRIMARY KEY,
              course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
              lesson_index INT NOT NULL,
              title TEXT NOT NULL,
              video_url TEXT NOT NULL,
              materials_url TEXT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE(course_id, lesson_index)
            );
        
            CREATE TABLE IF NOT EXISTS enrollments (
              user_id BIGINT NOT NULL,
              course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
              started_at TIMESTAMPTZ NOT NULL,
              next_lesson_index INT NOT NULL,
              next_lesson_at TIMESTAMPTZ NOT NULL,
              last_sent_at TIMESTAMPTZ NULL,
              PRIMARY KEY (user_id, course_id)
            );
        
            CREATE INDEX IF NOT EXISTS idx_enrollments_due ON enrollments(next_lesson_at);

            CREATE TABLE IF NOT EXISTS user_plans (
              user_id BIGINT PRIMARY KEY,
              plan TEXT NOT NULL,                 -- 'mixed' | 'live'
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );  
        
        CREATE TABLE IF NOT EXISTS courses (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          welcome_video_url TEXT,
          lesson_interval_days INT NOT NULL DEFAULT 7,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        
        CREATE TABLE IF NOT EXISTS lessons (
          id SERIAL PRIMARY KEY,
          course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
          lesson_index INT NOT NULL,
          title TEXT NOT NULL,
          video_url TEXT NOT NULL,
          materials_url TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(course_id, lesson_index)
        );
        
        CREATE TABLE IF NOT EXISTS enrollments (
          user_id BIGINT NOT NULL,
          course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
          started_at TIMESTAMPTZ NOT NULL,
          next_lesson_index INT NOT NULL,
          next_lesson_at TIMESTAMPTZ NOT NULL,
          last_sent_at TIMESTAMPTZ,
          PRIMARY KEY (user_id, course_id)
        );
        
        CREATE INDEX IF NOT EXISTS idx_enrollments_due
        ON enrollments (next_lesson_at);
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

    # -------- Payments --------
    def create_payment(self, user_id: int, provider: str, amount_cents: int, currency: str, plan: str | None = None) -> str:
        pid = _new_id()
        idem = pid  # stable idempotency key for this internal payment

        sql = """
        INSERT INTO payments (
            id, user_id, provider, status, amount_cents, currency, plan,
            external_id, idempotency_key, pay_url, raw_meta,
            pix_qr_base64, pix_copy_paste,
            created_at, paid_at
        )
        VALUES (
            %s, %s, %s, 'pending', %s, %s, %s,
            NULL, %s, NULL, NULL,
            NULL, NULL,
            %s, NULL
        )
        """
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (pid, user_id, provider, amount_cents, currency, plan, idem, now_utc()))
            con.commit()
        return pid

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

    def attach_checkout_details(self, *, payment_id: str, external_id: str, pay_url: Optional[str], raw_meta: Optional[dict]) -> None:
        # psycopg cannot adapt plain dict by default for JSONB, wrap it.
        meta_val = None
        if raw_meta is not None:
            meta_val = Json(raw_meta)

        sql = """
        UPDATE payments
        SET external_id=%s, pay_url=%s, raw_meta=%s
        WHERE id=%s
        """
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (external_id, pay_url, meta_val, payment_id))
            con.commit()

    def get_payment(self, payment_id: str) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM payments WHERE id=%s"
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (payment_id,))
                return cur.fetchone()

    def find_payment_by_external_id(self, *, provider: str, external_id: str) -> Optional[Dict[str, Any]]:
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

    def upsert_course(self, *, course_id: str, title: str, welcome_video_url: str, lesson_interval_days: int) -> None:
        sql = """
        INSERT INTO courses (id, title, welcome_video_url, lesson_interval_days)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
          title = EXCLUDED.title,
          welcome_video_url = EXCLUDED.welcome_video_url,
          lesson_interval_days = EXCLUDED.lesson_interval_days;
        """
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (course_id, title, welcome_video_url, lesson_interval_days))
            con.commit()

    def add_lesson(self, *, course_id: str, lesson_index: int, title: str, video_url: str, materials_url: Optional[str]) -> None:
        sql = """
        INSERT INTO lessons (course_id, lesson_index, title, video_url, materials_url)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (course_id, lesson_index) DO UPDATE SET
          title = EXCLUDED.title,
          video_url = EXCLUDED.video_url,
          materials_url = EXCLUDED.materials_url;
        """
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (course_id, lesson_index, title, video_url, materials_url))
            con.commit()

    def get_course(self, *, course_id: str) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM courses WHERE id=%s"
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (course_id,))
                return cur.fetchone()

    def get_lesson(self, *, course_id: str, lesson_index: int) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM lessons WHERE course_id=%s AND lesson_index=%s"
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (course_id, lesson_index))
                return cur.fetchone()

    def ensure_enrollment(self, *, user_id: int, course_id: str) -> Dict[str, Any]:
        # Если нет enrollment, создаём с next_lesson_index=1 "сейчас"
        course = self.get_course(course_id=course_id)
        if not course:
            raise RuntimeError(f"Course not found: {course_id}")

        sql = """
        INSERT INTO enrollments (user_id, course_id, started_at, next_lesson_index, next_lesson_at, last_sent_at)
        VALUES (%s, %s, %s, %s, %s, NULL)
        ON CONFLICT (user_id, course_id) DO NOTHING;
        """
        now = now_utc()
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (user_id, course_id, now, 1, now))
            con.commit()

        return self.get_enrollment(user_id=user_id, course_id=course_id)

    def get_enrollment(self, *, user_id: int, course_id: str) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM enrollments WHERE user_id=%s AND course_id=%s"
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (user_id, course_id))
                return cur.fetchone()

    def list_due_enrollments(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        sql = """
        SELECT * FROM enrollments
        WHERE next_lesson_at <= %s
        ORDER BY next_lesson_at ASC
        LIMIT %s
        """
        now = now_utc()
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (now, limit))
                return cur.fetchall() or []

    def advance_enrollment_after_sent(self, *, user_id: int, course_id: str, next_lesson_index: int, lesson_interval_days: int) -> None:
        now = now_utc()
        next_at = now + timedelta(days=int(lesson_interval_days))
        sql = """
        UPDATE enrollments
        SET next_lesson_index=%s, next_lesson_at=%s, last_sent_at=%s
        WHERE user_id=%s AND course_id=%s
        """
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (next_lesson_index, next_at, now, user_id, course_id))
            con.commit()


    def set_user_plan(self, *, user_id: int, plan: str) -> None:
        sql = """        INSERT INTO user_plans (user_id, plan, updated_at)
        VALUES (%s, %s, now())
        ON CONFLICT (user_id) DO UPDATE SET
          plan = EXCLUDED.plan,
          updated_at = EXCLUDED.updated_at;
        """
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (user_id, plan))
            con.commit()

    def get_user_plan(self, *, user_id: int) -> str:
        sql = "SELECT plan FROM user_plans WHERE user_id=%s"
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (user_id,))
                row = cur.fetchone()
        return (row["plan"] if row else "mixed")  # default


    def get_latest_pending_payment(self, user_id: int) -> dict | None:
        """
        Returns the latest pending payment for the user, or None.
        Pending = status in ('pending', 'created') depending on your statuses.
        """
        with  self.connect().cursor() as cur:
            cur.execute(
                """
                SELECT id, provider, amount_cents, currency, plan, pay_url, created_at
                FROM payments
                WHERE user_id = %s
                  AND status = 'pending'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d.name for d in cur.description]
            return dict(zip(cols, row))
