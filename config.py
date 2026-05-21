import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
_raw_admin_ids = os.environ.get("ADMIN_IDS", "")
try:
    ADMIN_IDS: list[int] = [int(x.strip()) for x in _raw_admin_ids.split(",") if x.strip()]
except ValueError as e:
    raise ValueError(f"ADMIN_IDS must be comma-separated integers, got: {_raw_admin_ids!r}") from e
if not ADMIN_IDS:
    raise ValueError("ADMIN_IDS must contain at least one admin Telegram user ID")
DB_PATH: str = os.environ.get("DB_PATH", str(Path(__file__).parent / "alumni_bot.db"))
