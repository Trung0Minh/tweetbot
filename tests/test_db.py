from datetime import UTC, datetime

import libsql
import pytest

from app.db import Database
from app.models import XUser


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


async def test_turso_persists_state_without_local_container_storage(tmp_path, monkeypatch):
    connect = libsql.connect
    cloud_path = tmp_path / "cloud.db"
    monkeypatch.setattr(libsql, "connect", lambda *args, **kwargs: connect(str(cloud_path)))
    credentials = {
        "turso_database_url": "libsql://tweetbot-test.turso.io",
        "turso_auth_token": "test-token",
    }
    db = Database(tmp_path / "container-one" / "bot.db", **credentials)
    await db.connect()
    await db.initialize()
    user = XUser("x-1", "example")
    checked_at = datetime(2026, 10, 15, tzinfo=UTC)
    try:
        await db.ensure_tracked_user(user, "100")
        result = await db.upsert_subscription(
            guild_id=1,
            channel_id=2,
            user=user,
            include_reposts=True,
            ping_role_id=3,
            start_after_post_id="100",
        )
        await db.mark_sent(result.subscription.id, "101")
        await db.update_poll_state(
            user.id, last_seen_post_id="101", checked_at=checked_at, successful=True
        )
    finally:
        await db.close()

    reopened = Database(tmp_path / "container-two" / "bot.db", **credentials)
    await reopened.connect()
    await reopened.initialize()
    try:
        assert await reopened.counts() == (1, 1)
        subscription = (await reopened.list_subscriptions(1))[0]
        assert subscription.start_after_post_id == "100"
        assert subscription.include_reposts is True
        assert subscription.ping_role_id == 3
        assert await reopened.was_sent(subscription.id, "101") is True
        tracked = await reopened.get_tracked_user(user.id)
        assert tracked.last_seen_post_id == "101"
        assert tracked.last_successful_poll_at == checked_at
        assert not db.path.parent.exists()
        assert not reopened.path.parent.exists()
    finally:
        await reopened.close()


async def test_turso_failure_never_falls_back_to_an_empty_local_database(tmp_path, monkeypatch):
    def fail_connect(*args, **kwargs):
        raise RuntimeError("sensitive database authentication response")

    monkeypatch.setattr(libsql, "connect", fail_connect)
    db = Database(
        tmp_path / "local.db",
        turso_database_url="libsql://tweetbot-test.turso.io",
        turso_auth_token="test-token",
    )
    with pytest.raises(RuntimeError, match="Could not connect to Turso") as caught:
        await db.connect()
    assert caught.value.__suppress_context__ is True
    assert not db.path.exists()


async def test_unchanged_username_does_not_write_or_reset_cursor(database):
    user = XUser("x-1", "example")
    await database.ensure_tracked_user(user, "100")
    await database.upsert_subscription(
        guild_id=1,
        channel_id=2,
        user=user,
        include_reposts=False,
        ping_role_id=None,
        start_after_post_id="100",
    )
    before = await database._fetch_all("SELECT total_changes() AS count")
    await database.ensure_tracked_user(user, "999")
    after = await database._fetch_all("SELECT total_changes() AS count")
    assert after == before
    await database.ensure_tracked_user(XUser(user.id, "newname"), "999")
    assert (await database.get_tracked_user(user.id)).last_seen_post_id == "100"
    assert (await database.list_subscriptions(1))[0].x_username == "newname"
