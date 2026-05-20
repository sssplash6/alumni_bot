import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
ADMIN_ID: int = int(os.environ["ADMIN_ID"])
DB_PATH: str = os.environ.get("DB_PATH", str(Path(__file__).parent / "alumni_bot.db"))
