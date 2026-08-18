from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from app.config import Settings
from app.db import Database
from app.discord_bot import DiscordFeedBot
from app.logging_config import configure_logging
from app.twikit_service import TwikitXService

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    logger.info("Bot starting")

    database = Database(settings.database_path)
    x_service = TwikitXService(
        settings.x_username,
        settings.x_email,
        settings.x_password,
        settings.x_cookies_path,
    )
    bot: DiscordFeedBot | None = None
    try:
        await database.connect()
        await database.initialize()
        logger.info("Database initialized")
        await x_service.authenticate()
        logger.info("X session loaded")

        bot = DiscordFeedBot(database, x_service, settings.poll_interval_seconds)
        loop = asyncio.get_running_loop()
        for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                shutdown_signal,
                lambda: asyncio.create_task(bot.close()),
            )
        await bot.start(settings.discord_token)
    finally:
        logger.info("Bot shutting down")
        if bot is not None and not bot.is_closed():
            await bot.close()
        await x_service.close()
        await database.close()


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        asyncio.run(run())
