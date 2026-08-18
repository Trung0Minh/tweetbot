from __future__ import annotations

from urllib.parse import urlsplit

import discord

from app.models import Subscription, XPost

_WEBHOOK_NAME = "TweetBot Relay"
_X_AVATAR_HOSTS = {"pbs.twimg.com", "abs.twimg.com"}


class DiscordSender:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self._webhooks: dict[int, discord.Webhook] = {}

    async def send(self, subscription: Subscription, post: XPost) -> None:
        channel = self.bot.get_channel(subscription.channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(subscription.channel_id)
        if not hasattr(channel, "webhooks") or not hasattr(channel, "create_webhook"):
            raise RuntimeError(f"Discord channel {subscription.channel_id} cannot receive messages")

        content = f"[Tweeted]({post.url})"
        roles: list[discord.Object] = []
        if subscription.ping_role_id is not None:
            content = f"<@&{subscription.ping_role_id}>\n{content}"
            roles.append(discord.Object(id=subscription.ping_role_id))

        allowed_mentions = discord.AllowedMentions(
            everyone=False,
            users=False,
            roles=roles,
            replied_user=False,
        )
        webhook = await self._get_webhook(channel, subscription.channel_id)
        try:
            await webhook.send(
                content,
                username=self._webhook_username(post),
                avatar_url=self._webhook_avatar_url(post.avatar_url),
                allowed_mentions=allowed_mentions,
            )
        except discord.NotFound:
            self._webhooks.pop(subscription.channel_id, None)
            raise

    async def _get_webhook(self, channel, channel_id: int) -> discord.Webhook:
        cached = self._webhooks.get(channel_id)
        if cached is not None:
            return cached

        bot_user = self.bot.user
        for webhook in await channel.webhooks():
            owner = getattr(webhook, "user", None)
            if (
                webhook.name == _WEBHOOK_NAME
                and bot_user is not None
                and owner is not None
                and owner.id == bot_user.id
                and webhook.token is not None
            ):
                self._webhooks[channel_id] = webhook
                return webhook

        webhook = await channel.create_webhook(
            name=_WEBHOOK_NAME,
            reason="Send X post links with the source account identity",
        )
        self._webhooks[channel_id] = webhook
        return webhook

    @staticmethod
    def _webhook_username(post: XPost) -> str:
        display_name = post.display_name.strip()
        return (display_name or f"@{post.username}")[:80]

    @staticmethod
    def _webhook_avatar_url(avatar_url: str | None) -> str | None:
        if avatar_url is None:
            return None
        parsed = urlsplit(avatar_url)
        if parsed.scheme == "https" and parsed.hostname in _X_AVATAR_HOSTS:
            return avatar_url
        return None
