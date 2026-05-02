"""
config.py — Loads all settings from .env
All credentials are read from the .env file; nothing is hard-coded.
"""

import os
from dotenv import load_dotenv

load_dotenv(override=True)  # override=True ensures .env wins over any system env vars


def _require(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is missing or empty. "
            f"Please set it in your .env file."
        )
    return value


# Telegram user account (userbot)
API_ID: int = int(_require("TELEGRAM_API_ID"))
API_HASH: str = _require("TELEGRAM_API_HASH")
PHONE_NUMBER: str = _require("TELEGRAM_PHONE")
if ":" in PHONE_NUMBER:
    raise EnvironmentError(
        "TELEGRAM_PHONE looks like a bot token (it contains ':')! "
        "Check your .env file — TELEGRAM_PHONE should be a phone number like +1234567890."
    )
SESSION_NAME: str = os.getenv("SESSION_NAME", "userbot_session")

# Telegram bot token (for the control UI)
BOT_TOKEN: str = _require("BOT_TOKEN")
BOT_SESSION_NAME: str = os.getenv("BOT_SESSION_NAME", "control_bot_session")

# MongoDB
MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "telegram_bot")

# Bot behaviour
# TARGET_CHAT_ID is the global fallback delivery target.
# Each user can override this per-account via the bot UI (📍 Set Target).
TARGET_CHAT_ID: str = os.getenv("TARGET_CHAT_ID", "me")
LINKS_PER_FILE: int = int(os.getenv("LINKS_PER_FILE", "100"))
HISTORY_MONTHS: int = int(os.getenv("HISTORY_MONTHS", "3"))

# Chats to EXCLUDE from monitoring (blacklist).
# The bot automatically monitors ALL groups/channels the user is in.
# Add any chat username (@name) or numeric ID here to skip it.
_raw_excluded = os.getenv("EXCLUDED_CHATS", "").strip()
EXCLUDED_CHATS: set[str] = {
    c.strip().lstrip("@").lower()
    for c in _raw_excluded.split(",")
    if c.strip()
}
