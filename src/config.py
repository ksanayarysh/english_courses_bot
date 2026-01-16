import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    channel_id: str
    database_url: str
    admin_ids: set[int]

    mp_access_token: str
    mp_webhook_secret: str  # опционально, если будешь проверять подпись
    price_cents: int


def load_config() -> Config:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    channel_id = os.getenv("CHANNEL_ID", "").strip()
    database_url = os.getenv("DATABASE_URL", "").strip()
    mp_access_token = os.getenv("MP_ACCESS_TOKEN", "").strip()
    mp_webhook_secret = os.getenv("MP_WEBHOOK_SECRET", "").strip()
    price_cents = int(os.getenv("PRICE_CENTS", "2990").strip())

    admin_ids: set[int] = set()
    raw_admins = os.getenv("ADMIN_IDS", "").strip()
    if raw_admins:
        for x in raw_admins.split(","):
            x = x.strip()
            if x.isdigit():
                admin_ids.add(int(x))

    if not bot_token:
        raise RuntimeError("BOT_TOKEN is missing in .env")
    if not channel_id:
        raise RuntimeError("CHANNEL_ID is missing in .env")
    if not database_url:
        raise RuntimeError("DATABASE_URL is missing in .env")
    if not admin_ids:
        raise RuntimeError("ADMIN_IDS is missing in .env")
    if not mp_access_token:
        raise RuntimeError("MP_ACCESS_TOKEN is missing in .env")

    return Config(
        bot_token=bot_token,
        channel_id=channel_id,
        database_url=database_url,
        admin_ids=admin_ids,
        mp_access_token=mp_access_token,
        mp_webhook_secret=mp_webhook_secret,
        price_cents=price_cents,
    )
