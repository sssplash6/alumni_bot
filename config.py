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
del _raw_admin_ids
DB_PATH: str = os.environ.get("DB_PATH", str(Path(__file__).parent / "alumni_bot.db"))

# The @username users are told to contact when the bot can't help them — most
# importantly when it has just refused them, where "contact an admin" leaves a
# person who believes they belong with nowhere to go. Lives here, not in a copy
# module, because all three features quote it and every one of them already
# imports config.
ADMIN_CONTACT: str = os.environ.get("ADMIN_CONTACT", "@gapyearingdoesntsuck").strip()

# ── Database backups ────────────────────────────────────────────────────────────
# The DB holds every mentor/mentee application, fair submission and alumni-gate
# classification. None of it is recoverable from Telegram, so snapshots are
# cheap insurance.
BACKUP_DIR: str = os.environ.get("BACKUP_DIR", str(Path(__file__).parent / "backups"))

# How often (hours) to snapshot the DB. 0 disables the job (/backup still works).
BACKUP_INTERVAL_HOURS: int = int(os.environ.get("BACKUP_INTERVAL_HOURS", "24") or "24")

# How many snapshots to keep; the oldest beyond this are pruned.
BACKUP_KEEP: int = int(os.environ.get("BACKUP_KEEP", "14") or "14")
