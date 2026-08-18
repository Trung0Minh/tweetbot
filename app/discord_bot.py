from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from app.commands import FeedCommands
from app.db import Database
from app.delivery import DiscordSender
from app.feed_manager import FeedManager
from app.watcher import Watcher
from app.x_service import XService

logger = logging.getLogger(__name__)


class DiscordFeedBot(commands.Bot):
    def __init__(self, database: Database, x_service: XService, poll_interval_seconds: int) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            help_command=None,
            intents=intents,
        )
        self.database = database
        self.x_service = x_service
        self.feed_manager = FeedManager(database, x_service)
        self.watcher = Watcher(
            database,
            x_service,
            DiscordSender(self),
            poll_interval_seconds,
        )
        self._watcher_task: asyncio.Task[None] | None = None
        self._commands_installed = False

    async def setup_hook(self) -> None:
        await self.install_commands(sync=True)

    async def install_commands(self, *, sync: bool) -> None:
        if not self._commands_installed:
            await self.add_cog(FeedCommands(self.database, self.feed_manager, self.watcher))
            self._commands_installed = True
        if sync:
            synced = await self.tree.sync()
            logger.info("Synced %d slash commands", len(synced))

    async def on_ready(self) -> None:
        logger.info("Discord connected as %s", self.user)
        if self._watcher_task is None or self._watcher_task.done():
            self._watcher_task = asyncio.create_task(self.watcher.run(), name="x-watcher")

    async def close(self) -> None:
        self.watcher.stop()
        if self._watcher_task is not None:
            await self._watcher_task
        await super().close()
