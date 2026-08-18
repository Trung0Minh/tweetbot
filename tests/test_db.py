from datetime import UTC, datetime

import pytest_asyncio

from app.db import Database
from app.models import XUser


@pytest_asyncio.fixture
async def database(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.connect()
    await db.initialize()
    yield db
    await db.close()


async def test_subscription_upsert_preserves_creation_boundary(database):
    user = XUser(id="x-1", username="Example")
    await database.ensure_tracked_user(user, initial_post_id="100")

    created = await database.upsert_subscription(
        guild_id=1,
        channel_id=2,
        user=user,
        include_reposts=False,
        ping_role_id=None,
        start_after_post_id="100",
    )
    updated = await database.upsert_subscription(
        guild_id=1,
        channel_id=2,
        user=user,
        include_reposts=True,
        ping_role_id=3,
        start_after_post_id="999",
    )

    assert created.created is True
    assert updated.created is False
    assert updated.subscription.id == created.subscription.id
    assert updated.subscription.include_reposts is True
    assert updated.subscription.ping_role_id == 3
    assert updated.subscription.start_after_post_id == "100"


async def test_sent_post_deduplication_survives_reconnect(tmp_path):
    path = tmp_path / "bot.db"
    db = Database(path)
    await db.connect()
    await db.initialize()
    user = XUser(id="x-1", username="example")
    await db.ensure_tracked_user(user, initial_post_id="100")
    result = await db.upsert_subscription(
        guild_id=1,
        channel_id=2,
        user=user,
        include_reposts=False,
        ping_role_id=None,
        start_after_post_id="100",
    )

    assert await db.mark_sent(result.subscription.id, "101") is True
    assert await db.mark_sent(result.subscription.id, "101") is False
    await db.close()

    reopened = Database(path)
    await reopened.connect()
    assert await reopened.was_sent(result.subscription.id, "101") is True
    await reopened.close()


async def test_removing_last_subscription_removes_tracked_user(database):
    user = XUser(id="x-1", username="example")
    await database.ensure_tracked_user(user, initial_post_id="100")
    await database.upsert_subscription(
        guild_id=1,
        channel_id=2,
        user=user,
        include_reposts=False,
        ping_role_id=None,
        start_after_post_id="100",
    )

    removed = await database.remove_subscription(1, 2, "EXAMPLE")

    assert removed is True
    assert await database.get_tracked_users() == []


async def test_poll_state_is_persisted(database):
    user = XUser(id="x-1", username="example")
    await database.ensure_tracked_user(user, initial_post_id="100")
    checked_at = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)

    await database.update_poll_state(
        user.id,
        last_seen_post_id="105",
        checked_at=checked_at,
        successful=True,
    )

    tracked = (await database.get_tracked_users())[0]
    assert tracked.last_seen_post_id == "105"
    assert tracked.last_checked_at == checked_at
    assert tracked.last_successful_poll_at == checked_at
