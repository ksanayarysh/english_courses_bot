import os
from dataclasses import dataclass
from locale import currency

from dotenv import load_dotenv

load_dotenv()

PRICES = {
    "live_only": {
        "BRL": int(os.getenv("PRICE_LIVE_ONLY_BRL")),
        "RUB": int(os.getenv("PRICE_LIVE_ONLY_RUB")),
    },
    "mixed": {
        "BRL": int(os.getenv("PRICE_MIXED_BRL")),
        "RUB": int(os.getenv("PRICE_MIXED_RUB")),
    },
}


@dataclass(frozen=True)
class Config:
    bot_token: str
    channel_id: str
    database_url: str
    admin_ids: set[int]
    admin_chat_id: int

    card_transfer_number: str
    card_transfer_holder: str

    course_id: str
    course_title: str
    welcome_video_url: str
    lesson_interval_days: int

    # Payments (MercadoPago Pix)
    mp_access_token: str
    mp_webhook_secret: str  # optional, but recommended
    # Payments (YooKassa)
    yk_shop_id: str
    yk_secret_key: str

    # Payments
    pay_provider_default: str  # pix | yookassa | mock

    # Webhooks
    public_base_url: str    # e.g. https://your-service.up.railway.app

    currency: str = "BRL"
    subscription_days: int = 30

    def price_for_plan_currency_cents(self, plan: str, currency: str) -> int:
        key = f"PRICE_{plan.upper()}_{currency.upper()}"
        # plan у тебя "live_only"/"mixed", значит ключи станут:
        # PRICE_LIVE_ONLY_BRL, PRICE_LIVE_ONLY_RUB, PRICE_MIXED_BRL, PRICE_MIXED_RUB
        v = os.getenv(key)
        if not v:
            raise RuntimeError(f"Missing env var: {key}")
        return int(v)

    def payment_description(self, plan: str) -> str:
        if plan == "live_only":
            return "Курс: все занятия online (1 месяц)"
        return "Курс: online + видео (1 месяц)"


def load_config() -> Config:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    channel_id = os.getenv("CHANNEL_ID", "").strip()
    database_url = os.getenv("DATABASE_URL", "").strip()

    course_id = os.getenv("COURSE_ID", "english_basic").strip()
    course_title = os.getenv("COURSE_TITLE", "English Course (Basic)").strip()
    welcome_video_url = os.getenv("WELCOME_VIDEO_URL", "https://youtu.be/FAKE_WELCOME").strip()
    lesson_interval_days = int(os.getenv("LESSON_INTERVAL_DAYS", "7"))

    admin_ids: set[int] = set()
    raw_admins = os.getenv("ADMIN_IDS", "").strip()
    if raw_admins:
        for x in raw_admins.split(","):
            x = x.strip()
            if x.isdigit():
                admin_ids.add(int(x))

    admin_chat_id = int(os.getenv("ADMIN_CHAT_ID", "0").strip() or 0)
    if not admin_chat_id:
        admin_chat_id = next(iter(admin_ids), 0)

    card_transfer_number = os.getenv("CARD_TRANSFER_NUMBER", "").strip()
    card_transfer_holder = os.getenv("CARD_TRANSFER_HOLDER", "").strip()

    mp_access_token = os.getenv("MP_ACCESS_TOKEN", "").strip()
    mp_webhook_secret = os.getenv("MP_WEBHOOK_SECRET", "").strip()

    yk_shop_id = os.getenv("YK_SHOP_ID", "").strip()
    yk_secret_key = os.getenv("YK_SECRET_KEY", "").strip()


    pay_provider_default = os.getenv("PAY_PROVIDER_DEFAULT", "pix").strip().lower()

    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    subscription_days = int(os.getenv("SUBSCRIPTION_DAYS", "30").strip())

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
        admin_chat_id=admin_chat_id,
        mp_access_token=mp_access_token,
        mp_webhook_secret=mp_webhook_secret,
        yk_shop_id=yk_shop_id,
        yk_secret_key=yk_secret_key,
        pay_provider_default=pay_provider_default,
        public_base_url=public_base_url,
        course_id=course_id,
        course_title=course_title,
        welcome_video_url=welcome_video_url,
        lesson_interval_days=lesson_interval_days,
        card_transfer_number=card_transfer_number,
        card_transfer_holder=card_transfer_holder,
        subscription_days=subscription_days
    )
