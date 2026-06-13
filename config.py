import os
import secrets
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN        = os.getenv("BOT_TOKEN")
ADMIN_IDS        = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
GOOGLE_SHEET_ID  = os.getenv("GOOGLE_SHEET_ID")
GEMINI_KEY       = os.getenv("GEMINI_API_KEY")
FORM_URL         = os.getenv("FORM_URL", "")
ADMIN_USERNAME   = os.getenv("ADMIN_USERNAME", "")  # e.g. "yourname" without @

BITO_CHANNEL_ID   = int(os.getenv("BITO_CHANNEL_ID") or 0)
BI_CHANNEL_ID     = int(os.getenv("BI_CHANNEL_ID") or 0)
BITO_CHANNEL_LINK = os.getenv("BITO_CHANNEL_LINK", "")
BI_CHANNEL_LINK   = os.getenv("BI_CHANNEL_LINK", "")

REG_CODES: dict[str, int] = {}   # runtime state — stays in code, not .env


def admin_contact() -> str:
    """Returns '@username' if ADMIN_USERNAME is set, else empty string."""
    return f"@{ADMIN_USERNAME}" if ADMIN_USERNAME else ""


def make_code(telegram_id: int) -> str:
    # Cryptographically random 8-char code — different on every /start call.
    # The telegram_id param is kept for API compatibility but is not used.
    return secrets.token_urlsafe(6).upper()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def validate_env():
    """Raise EnvironmentError early if any required env var is missing."""
    required = {
        "BOT_TOKEN": BOT_TOKEN,
        "GOOGLE_SHEET_ID": GOOGLE_SHEET_ID,
        "ADMIN_IDS": os.getenv("ADMIN_IDS"),
        "GEMINI_API_KEY": GEMINI_KEY,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "See .env.example for the full list."
        )
    if FORM_URL:
        try:
            FORM_URL.format("test")
        except (IndexError, KeyError, ValueError) as e:
            raise EnvironmentError(f"FORM_URL format string is invalid: {e}")