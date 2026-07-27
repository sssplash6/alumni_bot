# main.py
import asyncio
import logging

from telegram import Update

import gate
from bot import build_app
from database import init_db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


async def main() -> None:
    await init_db()
    # The gate owns its own tables, so it creates them itself.
    await gate.init_schema()
    # Which groups it watches lives in the database (managed with /gate_watch),
    # so the in-memory list has to be primed before the first update arrives.
    watching = await gate.load_monitored()
    logging.getLogger(__name__).info("Alumni gate watching %d group(s)", len(watching))
    app = build_app()
    await app.initialize()
    await app.start()
    # ALL_TYPES so Telegram also delivers chat_member (join) updates, which the
    # Alumni Gate needs.
    await app.updater.start_polling(
        drop_pending_updates=True, allowed_updates=Update.ALL_TYPES
    )
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
