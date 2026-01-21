from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List

from psycopg.rows import dict_row
import psycopg


DDL = """
CREATE TABLE IF NOT EXISTS live_sessions (
  id SERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  starts_at TIMESTAMPTZ NOT NULL,
  title TEXT NOT NULL DEFAULT 'Практика',
  meeting_url TEXT,

  remind_24h_sent_at TIMESTAMPTZ,
  remind_1h_sent_at TIMESTAMPTZ,
  remind_15m_sent_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_live_sessions_starts_at ON live_sessions (starts_at);
CREATE INDEX IF NOT EXISTS idx_live_sessions_user ON live_sessions (user_id);
"""


@dataclass(frozen=True)
class LiveSession:
    id: int
    user_id: int
    starts_at: datetime
    title: str
    meeting_url: str


class LiveSessionsRepo:
    """DB access layer for scheduling live (practice) sessions + reminders.

    Keeps the rest of your codebase blissfully unaware of SQL details.
    """

    def __init__(self, database_url: str):
        self.database_url = database_url

    def connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def init_schema(self) -> None:
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(DDL)
            con.commit()

    def add_session(
        self,
        *,
        user_id: int,
        starts_at: datetime,
        title: str = "Практика",
        meeting_url: str = "",
    ) -> int:
        sql = """
        INSERT INTO live_sessions (user_id, starts_at, title, meeting_url)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (user_id, starts_at, title, meeting_url))
                row = cur.fetchone()
            con.commit()
        return int(row["id"])

    # ----- Due queries -----
    def list_live_sessions_due_24h(self, now: datetime) -> List[Dict[str, Any]]:
        # Due when start is within next 24h AND not already sent.
        horizon = now + timedelta(hours=24)
        sql = """
        SELECT id, user_id, starts_at, title, meeting_url
        FROM live_sessions
        WHERE starts_at > %s
          AND starts_at <= %s
          AND remind_24h_sent_at IS NULL
        ORDER BY starts_at ASC
        LIMIT 200
        """
        return self._fetch(sql, (now, horizon))

    def list_live_sessions_due_1h(self, now: datetime) -> List[Dict[str, Any]]:
        horizon = now + timedelta(hours=1)
        sql = """
        SELECT id, user_id, starts_at, title, meeting_url
        FROM live_sessions
        WHERE starts_at > %s
          AND starts_at <= %s
          AND remind_1h_sent_at IS NULL
        ORDER BY starts_at ASC
        LIMIT 200
        """
        return self._fetch(sql, (now, horizon))

    def list_live_sessions_due_15m(self, now: datetime) -> List[Dict[str, Any]]:
        horizon = now + timedelta(minutes=15)
        sql = """
        SELECT id, user_id, starts_at, title, meeting_url
        FROM live_sessions
        WHERE starts_at > %s
          AND starts_at <= %s
          AND remind_15m_sent_at IS NULL
        ORDER BY starts_at ASC
        LIMIT 200
        """
        return self._fetch(sql, (now, horizon))

    def mark_live_session_reminded(self, session_id: int, kind: str) -> None:
        if kind not in {"24h", "1h", "15m"}:
            raise ValueError("kind must be one of: 24h, 1h, 15m")

        col = {
            "24h": "remind_24h_sent_at",
            "1h": "remind_1h_sent_at",
            "15m": "remind_15m_sent_at",
        }[kind]

        sql = f"UPDATE live_sessions SET {col}=NOW() WHERE id=%s"
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, (session_id,))
            con.commit()

    def _fetch(self, sql: str, params: tuple) -> List[Dict[str, Any]]:
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() or []
