from datetime import UTC, datetime
from types import SimpleNamespace

from app.commands import (
    can_manage_feeds,
    destination_error,
    format_action_results,
    format_status,
    format_subscriptions,
)
from app.feed_manager import FeedActionResult
from app.models import Subscription


async def test_manage_permission_accepts_administrator_or_manage_guild():
    administrator = SimpleNamespace(
        user=SimpleNamespace(
            guild_permissions=SimpleNamespace(administrator=True, manage_guild=False)
        )
    )
    manager = SimpleNamespace(
        user=SimpleNamespace(
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=True)
        )
    )
    ordinary = SimpleNamespace(
        user=SimpleNamespace(
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=False)
        )
    )

    assert await can_manage_feeds(administrator) is True
    assert await can_manage_feeds(manager) is True
    assert await can_manage_feeds(ordinary) is False


def test_destination_requires_manage_webhooks_permission():
    bot_member = object()
    guild = SimpleNamespace(id=1, me=bot_member)
    channel = SimpleNamespace(
        guild=guild,
        permissions_for=lambda member: SimpleNamespace(
            view_channel=True,
            send_messages=True,
            manage_webhooks=False,
        ),
    )
    interaction = SimpleNamespace(guild=guild)

    assert destination_error(channel, interaction) == (
        "The bot cannot manage webhooks in that channel."
    )


def test_destination_requires_embed_links_permission():
    bot_member = object()
    guild = SimpleNamespace(id=1, me=bot_member)
    channel = SimpleNamespace(
        guild=guild,
        permissions_for=lambda member: SimpleNamespace(
            view_channel=True,
            send_messages=True,
            manage_webhooks=True,
            embed_links=False,
        ),
    )
    interaction = SimpleNamespace(guild=guild)

    assert destination_error(channel, interaction) == (
        "The bot cannot embed links in that channel."
    )


def test_partial_success_results_are_reported_by_action():
    results = [
        FeedActionResult("Foo", True, "added"),
        FeedActionResult("Bar", True, "updated"),
        FeedActionResult("Missing", False, "failed", "account not found"),
    ]

    message = format_action_results(results)

    assert "Added:\n@Foo" in message
    assert "Updated:\n@Bar" in message
    assert "Failed:\n@Missing — account not found" in message


def test_follows_are_grouped_by_channel():
    now = datetime.now(UTC)
    subscriptions = [
        Subscription(1, 1, 10, "x1", "Foo", False, None, "100", now, now),
        Subscription(2, 1, 20, "x2", "Bar", True, 30, "200", now, now),
    ]

    message = format_subscriptions(subscriptions)

    assert "<#10>\n@Foo\nReposts: No\nPing: None" in message
    assert "<#20>\n@Bar\nReposts: Yes\nPing: <@&30>" in message


def test_status_uses_compact_discord_markdown_and_local_timestamp():
    last_poll = datetime(2026, 8, 18, 5, 19, 54, tzinfo=UTC)
    timestamp = int(last_poll.timestamp())

    message = format_status(
        running=True,
        tracked_count=1,
        subscription_count=2,
        poll_interval_seconds=60,
        last_successful_poll_at=last_poll,
    )

    assert message == (
        "**X Watcher** · 🟢 **Running**\n\n"
        "> **Tracked accounts:** `1`\n"
        "> **Discord subscriptions:** `2`\n"
        "> **Polling interval:** `60 seconds`\n"
        f"> **Last successful poll:** <t:{timestamp}:T> · <t:{timestamp}:R>"
    )


def test_status_formats_stopped_watcher_that_has_not_polled():
    message = format_status(
        running=False,
        tracked_count=0,
        subscription_count=0,
        poll_interval_seconds=90,
        last_successful_poll_at=None,
    )

    assert "🔴 **Stopped**" in message
    assert "**Last successful poll:** Never" in message
