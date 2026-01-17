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

    # Payments (MercadoPago Pix)
    mp_access_token: str
    mp_webhook_secret: str  # optional, but recommended
    price_cents: int

    # Payments (YooKassa)
    yk_shop_id: str
    yk_secret_key: str

    # Payments
    pay_provider_default: str  # pix | yookassa | mock

    # Webhooks
    public_base_url: str    # e.g. https://your-service.up.railway.app


def load_config() -> Config:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    channel_id = os.getenv("CHANNEL_ID", "").strip()
    database_url = os.getenv("DATABASE_URL", "").strip()

    admin_ids: set[int] = set()
    raw_admins = os.getenv("ADMIN_IDS", "").strip()
    if raw_admins:
        for x in raw_admins.split(","):
            x = x.strip()
            if x.isdigit():
                admin_ids.add(int(x))

    mp_access_token = os.getenv("MP_ACCESS_TOKEN", "").strip()
    mp_webhook_secret = os.getenv("MP_WEBHOOK_SECRET", "").strip()
    price_cents = int(os.getenv("PRICE_CENTS", "2990").strip())

    yk_shop_id = os.getenv("YK_SHOP_ID", "").strip()
    yk_secret_key = os.getenv("YK_SECRET_KEY", "").strip()

    pay_provider_default = os.getenv("PAY_PROVIDER_DEFAULT", "pix").strip().lower()

    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

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
    if not public_base_url:
        raise RuntimeError("PUBLIC_BASE_URL is missing in .env")

    # YooKassa keys are required only if provider is enabled
    if pay_provider_default == "yookassa" and (not yk_shop_id or not yk_secret_key):
        raise RuntimeError("YK_SHOP_ID / YK_SECRET_KEY are missing in .env")

    return Config(
        bot_token=bot_token,
        channel_id=channel_id,
        database_url=database_url,
        admin_ids=admin_ids,
        mp_access_token=mp_access_token,
        mp_webhook_secret=mp_webhook_secret,
        price_cents=price_cents,
        yk_shop_id=yk_shop_id,
        yk_secret_key=yk_secret_key,
        pay_provider_default=pay_provider_default,
        public_base_url=public_base_url,
    )
