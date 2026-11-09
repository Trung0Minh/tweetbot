from __future__ import annotations

import re
from urllib.parse import urlsplit

import discord

from app.models import Subscription, XPost

_WEBHOOK_NAME = "TweetBot Relay"
_X_AVATAR_HOSTS = {"pbs.twimg.com", "abs.twimg.com"}
_MAX_DESCRIPTION = 4096
_MAX_IMAGES = 4
_HASHTAG = re.compile(r"(?<![\w/])#(\w+)")
_MENTION = re.compile(r"(?<![\w/])@(\w+)")


def build_message(
    post: XPost, ping_role_id: int | None
) -> tuple[str | None, list[discord.Embed] | None]:
    """Return (content, embeds); embeds is None when the post falls back to a link."""
    ping = f"<@&{ping_role_id}>" if ping_role_id is not None else None
    if post.has_video or post.is_quote or post.is_repost:
        preview_url = f"https://fxtwitter.com/{post.username}/status/{post.id}"
        link = f"[Tweeted]({preview_url})"
        return (f"{ping}\n{link}" if ping else link), None

    text = _link_entities(post)
    if len(text) > _MAX_DESCRIPTION:
        text = post.text
    if len(text) > _MAX_DESCRIPTION:
        text = text[: _MAX_DESCRIPTION - 1] + "\u2026"
    embed = discord.Embed(description=text or None, timestamp=post.created_at)
    embed.set_author(
        name=f"@{post.username}",
        url=f"https://x.com/{post.username}/status/{post.id}",
        icon_url=_x_avatar_url(post.avatar_url),
    )
    embeds = [embed]
    for image_url in post.image_urls[:_MAX_IMAGES]:
        embeds.append(discord.Embed().set_image(url=image_url))
    return ping, embeds


def _link_entities(post: XPost) -> str:
    """Turn real hashtag/mention entities into Discord masked links."""
    hashtags = {h.lower() for h in post.hashtags}
    mentions = {m.lower() for m in post.mentions}

    def link(prefix: str, names: set[str], path: str):
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name.lower() not in names:
                return match.group(0)
            return f"[{prefix}{name}](https://x.com/{path}{name})"

        return replace

    text = _HASHTAG.sub(link("#", hashtags, "hashtag/"), post.text)
    return _MENTION.sub(link("@", mentions, ""), text)


def _x_avatar_url(avatar_url: str | None) -> str | None:
    if avatar_url is None:
        return None
    parsed = urlsplit(avatar_url)
    if parsed.scheme == "https" and parsed.hostname in _X_AVATAR_HOSTS:
        return avatar_url
    return None


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

        content, embeds = build_message(post, subscription.ping_role_id)
        roles: list[discord.Object] = []
        if subscription.ping_role_id is not None:
            roles.append(discord.Object(id=subscription.ping_role_id))

        allowed_mentions = discord.AllowedMentions(
            everyone=False,
            users=False,
            roles=roles,
            replied_user=False,
        )
        webhook = await self._get_webhook(channel, subscription.channel_id)
        try:
            extra = {"embeds": embeds} if embeds is not None else {}
            await webhook.send(
                content,
                **extra,
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
        return _x_avatar_url(avatar_url)
