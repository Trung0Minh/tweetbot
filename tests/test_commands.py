from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from discord import app_commands

from app.commands import (
    FeedCommands,
    can_follow_feeds,
    can_manage_feeds,
    destination_error,
    format_action_results,
    format_status,
    format_subscriptions,
)
from app.feed_manager import FeedActionResult
from app.models import Subscription, XUser


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


@pytest.mark.parametrize(
    ("role_names", "administrator", "manage_guild", "in_server", "allowed"),
    [
        (["The Almighty One"], False, False, True, True),
        (["Member"], False, False, True, False),
        (["the almighty one"], False, False, True, False),
        ([], True, False, True, True),
        ([], False, True, True, True),
        (["The Almighty One"], False, False, False, False),
    ],
)
async def test_follow_permission_requires_exact_role_or_management_permission(
    role_names, administrator, manage_guild, in_server, allowed
):
    interaction = SimpleNamespace(
        guild=object() if in_server else None,
        user=SimpleNamespace(
            roles=[SimpleNamespace(name=name) for name in role_names],
            guild_permissions=SimpleNamespace(
                administrator=administrator, manage_guild=manage_guild
            ),
        ),
    )

    assert await can_follow_feeds(interaction) is allowed
    if not administrator and not manage_guild:
        assert await can_manage_feeds(interaction) is False


def test_only_follow_uses_the_named_role_permission():
    assert FeedCommands.follow.checks == [can_follow_feeds]
    assert FeedCommands.unfollow.checks == [can_manage_feeds]


@pytest.mark.parametrize("command_name", ["follow", "unfollow"])
async def test_permission_error_mentions_role_only_for_follow(command_name):
    interaction = SimpleNamespace(
        command=SimpleNamespace(name=command_name),
        response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
    )
    commands = FeedCommands(None, None, None)

    await commands.cog_app_command_error(interaction, app_commands.CheckFailure())

    message = interaction.response.send_message.call_args.args[0]
    assert ('"The Almighty One"' in message) == (command_name == "follow")
    assert interaction.response.send_message.call_args.kwargs == {"ephemeral": True}


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
        tracked_usernames=["Example"],
        subscription_count=2,
        poll_interval_seconds=60,
        last_successful_poll_at=last_poll,
    )

    assert message == (
        "**X Watcher** · 🟢 **Running**\n\n"
        "> **Tracked accounts:** `1`\n"
        "> **Discord subscriptions:** `2`\n"
        "> **Polling interval:** `60 seconds`\n"
        f"> **Last successful poll:** <t:{timestamp}:T> · <t:{timestamp}:R>\n\n"
        "**Accounts in this server**\n`@Example`"
    )


def test_status_formats_stopped_watcher_that_has_not_polled():
    message = format_status(
        running=False,
        tracked_usernames=[],
        subscription_count=0,
        poll_interval_seconds=90,
        last_successful_poll_at=None,
    )

    assert "🔴 **Stopped**" in message
    assert "**Last successful poll:** Never" in message
    assert "**Accounts in this server**\nNone" in message


async def test_status_lists_unique_accounts_only_in_current_server(database):
    for user, guild_id, channel_id in [
        (XUser("x1", "Foo"), 1, 10),
        (XUser("x1", "Foo"), 1, 20),
        (XUser("x2", "Bar"), 1, 10),
        (XUser("x3", "OtherServer"), 2, 30),
    ]:
        await database.ensure_tracked_user(user, "100")
        await database.upsert_subscription(
            guild_id=guild_id,
            channel_id=channel_id,
            user=user,
            include_reposts=False,
            ping_role_id=None,
            start_after_post_id="100",
        )
    interaction = SimpleNamespace(
        guild_id=1,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    watcher = SimpleNamespace(running=True, poll_interval_seconds=60, last_successful_poll_at=None)
    commands = FeedCommands(database, None, watcher)

    await commands.status.callback(commands, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    message = interaction.followup.send.call_args.args[0]
    assert "**Tracked accounts:** `2`" in message
    assert "**Discord subscriptions:** `3`" in message
    assert "`@Bar`\n`@Foo`" in message
    assert message.count("@Foo") == 1
    assert "OtherServer" not in message
    assert interaction.followup.send.call_args.kwargs == {"ephemeral": True}


async def test_status_splits_long_account_lists_into_private_messages():
    subscriptions = [
        SimpleNamespace(x_user_id=str(i), x_username=f"account{i:08d}") for i in range(250)
    ]
    database = SimpleNamespace(list_subscriptions=AsyncMock(return_value=subscriptions))
    interaction = SimpleNamespace(
        guild_id=1,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    watcher = SimpleNamespace(running=True, poll_interval_seconds=60, last_successful_poll_at=None)
    commands = FeedCommands(database, None, watcher)

    await commands.status.callback(commands, interaction)

    calls = interaction.followup.send.call_args_list
    assert len(calls) > 1
    assert all(len(call.args[0]) <= 2000 and call.kwargs["ephemeral"] for call in calls)
    combined = "".join(call.args[0] for call in calls)
    assert all(f"@{subscription.x_username}" in combined for subscription in subscriptions)


async def test_status_rejects_direct_messages():
    interaction = SimpleNamespace(guild_id=None, response=SimpleNamespace(send_message=AsyncMock()))
    commands = FeedCommands(None, None, None)

    await commands.status.callback(commands, interaction)

    interaction.response.send_message.assert_awaited_once_with(
        "This command can only be used in a server.", ephemeral=True
    )
