from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from app.db import Database
from app.feed_manager import FeedActionResult, FeedManager, parse_usernames
from app.models import Subscription
from app.watcher import Watcher

logger = logging.getLogger(__name__)


async def can_manage_feeds(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and (permissions.administrator or permissions.manage_guild))


def format_action_results(results: list[FeedActionResult]) -> str:
    grouped: dict[str, list[str]] = defaultdict(list)
    for result in results:
        line = f"@{result.username}"
        if not result.success and result.error:
            line += f" — {result.error}"
        grouped[result.action].append(line)

    preferred_order = ("added", "updated", "removed", "failed", "not followed")
    sections = []
    for action in preferred_order:
        if grouped[action]:
            sections.append(f"{action.title()}:\n" + "\n".join(grouped[action]))
    return "\n\n".join(sections) or "No changes."


def format_subscriptions(subscriptions: list[Subscription]) -> str:
    if not subscriptions:
        return "No X feeds are configured."

    grouped: dict[int, list[Subscription]] = defaultdict(list)
    for subscription in subscriptions:
        grouped[subscription.channel_id].append(subscription)

    channel_sections = []
    for channel_id, channel_subscriptions in grouped.items():
        feeds = []
        for subscription in channel_subscriptions:
            ping = (
                f"<@&{subscription.ping_role_id}>"
                if subscription.ping_role_id is not None
                else "None"
            )
            feeds.append(
                f"@{subscription.x_username}\n"
                f"Reposts: {'Yes' if subscription.include_reposts else 'No'}\n"
                f"Ping: {ping}"
            )
        channel_sections.append(f"<#{channel_id}>\n" + "\n\n".join(feeds))
    return "\n\n".join(channel_sections)


def format_status(
    *,
    running: bool,
    tracked_count: int,
    subscription_count: int,
    poll_interval_seconds: int,
    last_successful_poll_at: datetime | None,
) -> str:
    state = "🟢 **Running**" if running else "🔴 **Stopped**"
    if last_successful_poll_at is None:
        last_poll = "Never"
    else:
        timestamp = int(last_successful_poll_at.timestamp())
        last_poll = f"<t:{timestamp}:T> · <t:{timestamp}:R>"
    return (
        f"**X Watcher** · {state}\n\n"
        f"> **Tracked accounts:** `{tracked_count}`\n"
        f"> **Discord subscriptions:** `{subscription_count}`\n"
        f"> **Polling interval:** `{poll_interval_seconds} seconds`\n"
        f"> **Last successful poll:** {last_poll}"
    )


def split_message(message: str, limit: int = 2000) -> list[str]:
    chunks: list[str] = []
    remaining = message
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    chunks.append(remaining)
    return chunks


def destination_error(channel: discord.TextChannel, interaction: discord.Interaction) -> str | None:
    if interaction.guild is None or channel.guild.id != interaction.guild.id:
        return "The destination channel must be in this server."
    bot_member = channel.guild.me
    if bot_member is None:
        return "The bot could not determine its permissions in that channel."
    permissions = channel.permissions_for(bot_member)
    if not permissions.view_channel:
        return "The bot cannot view that channel."
    if not permissions.send_messages:
        return "The bot cannot send messages in that channel."
    if not permissions.manage_webhooks:
        return "The bot cannot manage webhooks in that channel."
    if not permissions.embed_links:
        return "The bot cannot embed links in that channel."
    return None


class FeedCommands(commands.Cog):
    def __init__(self, database: Database, manager: FeedManager, watcher: Watcher) -> None:
        self.database = database
        self.manager = manager
        self.watcher = watcher

    @app_commands.command(name="follow", description="Forward new posts from X accounts")
    @app_commands.rename(feed_source="feed-source")
    @app_commands.describe(
        feed_source="Comma-separated X handles",
        channel="Discord text channel that receives links",
        reposts="Forward pure reposts",
        ping="Optional role to mention",
    )
    @app_commands.check(can_manage_feeds)
    async def follow(
        self,
        interaction: discord.Interaction,
        feed_source: str,
        channel: discord.TextChannel,
        reposts: bool = False,
        ping: discord.Role | None = None,
    ) -> None:
        error = destination_error(channel, interaction)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        if ping is not None and ping.guild.id != channel.guild.id:
            await interaction.response.send_message(
                "The ping role must be in the destination server.", ephemeral=True
            )
            return
        try:
            usernames = parse_usernames(feed_source)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        results = await self.manager.follow_many(
            usernames,
            interaction.guild_id,
            channel.id,
            reposts,
            ping.id if ping else None,
        )
        await interaction.followup.send(format_action_results(results), ephemeral=True)

    @app_commands.command(name="unfollow", description="Stop forwarding selected X accounts")
    @app_commands.rename(feed_source="feed-source")
    @app_commands.describe(
        feed_source="Comma-separated X handles",
        channel="Channel whose subscriptions should be removed",
    )
    @app_commands.check(can_manage_feeds)
    async def unfollow(
        self,
        interaction: discord.Interaction,
        feed_source: str,
        channel: discord.TextChannel,
    ) -> None:
        try:
            usernames = parse_usernames(feed_source)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        results = await self.manager.unfollow_many(usernames, interaction.guild_id, channel.id)
        await interaction.followup.send(format_action_results(results), ephemeral=True)

    @app_commands.command(name="follows", description="List X feeds in this server")
    @app_commands.describe(channel="Optionally limit the list to one channel")
    async def follows(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        subscriptions = await self.database.list_subscriptions(
            interaction.guild_id, channel.id if channel else None
        )
        chunks = split_message(format_subscriptions(subscriptions))
        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    @app_commands.command(name="status", description="Show X watcher status")
    async def status(self, interaction: discord.Interaction) -> None:
        tracked_count, subscription_count = await self.database.counts()
        message = format_status(
            running=self.watcher.running,
            tracked_count=tracked_count,
            subscription_count=subscription_count,
            poll_interval_seconds=self.watcher.poll_interval_seconds,
            last_successful_poll_at=self.watcher.last_successful_poll_at,
        )
        await interaction.response.send_message(message, ephemeral=True)

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            message = "You need Administrator or Manage Server permission to use this command."
        else:
            logger.error("Slash command failed", exc_info=error)
            message = "The command failed unexpectedly. Please check the bot logs."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
