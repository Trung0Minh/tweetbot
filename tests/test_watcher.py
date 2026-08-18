from collections import defaultdict

import pytest_asyncio

from app.db import Database
from app.models import XUser
from app.watcher import Watcher, posts_after_marker, should_deliver


class FakeXService:
    def __init__(self, posts):
        self.posts = posts
        self.fetches = defaultdict(int)

    async def get_recent_posts(self, user_id):
        self.fetches[user_id] += 1
        value = self.posts[user_id]
        if isinstance(value, Exception):
            raise value
        return value


class RecordingSender:
    def __init__(self, failing_channels=()):
        self.messages = []
        self.failing_channels = set(failing_channels)

    async def send(self, subscription, post):
        if subscription.channel_id in self.failing_channels:
            raise RuntimeError("channel unavailable")
        self.messages.append((subscription.channel_id, post.id))


@pytest_asyncio.fixture
async def database(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.connect()
    await db.initialize()
    yield db
    await db.close()


def test_posts_after_marker_are_oldest_first(post_factory):
    posts = [post_factory("103"), post_factory("102"), post_factory("101")]
    assert [post.id for post in posts_after_marker(posts, "100")] == ["101", "102", "103"]


def test_missing_non_numeric_marker_is_conservative(post_factory):
    posts = [post_factory("new", seconds=2), post_factory("older", seconds=1)]
    assert posts_after_marker(posts, "unknown") == []


def test_reply_repost_and_quote_filters(post_factory):
    assert should_deliver(post_factory("1", is_reply=True), include_reposts=True) is False
    assert should_deliver(post_factory("2", is_repost=True), include_reposts=False) is False
    assert should_deliver(post_factory("3", is_repost=True), include_reposts=True) is True
    assert should_deliver(post_factory("4", is_quote=True), include_reposts=False) is True


async def test_one_fetch_fans_out_chronologically_with_per_subscription_filters(
    database, post_factory
):
    user = XUser("x-1", "example")
    await database.ensure_tracked_user(user, "100")
    first = await database.upsert_subscription(
        guild_id=1,
        channel_id=10,
        user=user,
        include_reposts=False,
        ping_role_id=None,
        start_after_post_id="100",
    )
    await database.upsert_subscription(
        guild_id=1,
        channel_id=20,
        user=user,
        include_reposts=True,
        ping_role_id=None,
        start_after_post_id="100",
    )
    posts = [
        post_factory("103"),
        post_factory("102", is_repost=True),
        post_factory("101", is_reply=True),
        post_factory("100"),
    ]
    x_service = FakeXService({user.id: posts})
    sender = RecordingSender()

    await Watcher(database, x_service, sender, poll_interval_seconds=60).poll_once()

    assert x_service.fetches[user.id] == 1
    assert sender.messages == [(10, "103"), (20, "102"), (20, "103")]
    assert await database.was_sent(first.subscription.id, "103") is True


async def test_successful_deliveries_are_not_duplicated_after_restart(database, post_factory):
    user = XUser("x-1", "example")
    await database.ensure_tracked_user(user, "100")
    await database.upsert_subscription(
        guild_id=1,
        channel_id=10,
        user=user,
        include_reposts=False,
        ping_role_id=None,
        start_after_post_id="100",
    )
    posts = [post_factory("101"), post_factory("100")]
    x_service = FakeXService({user.id: posts})
    sender = RecordingSender()

    await Watcher(database, x_service, sender, 60).poll_once()
    await Watcher(database, x_service, sender, 60).poll_once()

    assert sender.messages == [(10, "101")]


async def test_failed_channel_does_not_block_other_channels_and_can_retry(database, post_factory):
    user = XUser("x-1", "example")
    await database.ensure_tracked_user(user, "100")
    for channel_id in (10, 20):
        await database.upsert_subscription(
            guild_id=1,
            channel_id=channel_id,
            user=user,
            include_reposts=False,
            ping_role_id=None,
            start_after_post_id="100",
        )
    posts = [post_factory("101"), post_factory("100")]
    x_service = FakeXService({user.id: posts})
    failing_sender = RecordingSender(failing_channels={10})

    await Watcher(database, x_service, failing_sender, 60).poll_once()

    assert failing_sender.messages == [(20, "101")]
    retry_sender = RecordingSender()
    await Watcher(database, x_service, retry_sender, 60).poll_once()
    assert retry_sender.messages == [(10, "101")]


async def test_new_subscription_does_not_receive_posts_before_its_boundary(database, post_factory):
    user = XUser("x-1", "example")
    await database.ensure_tracked_user(user, "100")
    await database.upsert_subscription(
        guild_id=1,
        channel_id=10,
        user=user,
        include_reposts=False,
        ping_role_id=None,
        start_after_post_id="100",
    )
    await database.upsert_subscription(
        guild_id=1,
        channel_id=20,
        user=user,
        include_reposts=False,
        ping_role_id=None,
        start_after_post_id="102",
    )
    posts = [
        post_factory("103"),
        post_factory("102"),
        post_factory("101"),
        post_factory("100"),
    ]
    sender = RecordingSender()

    await Watcher(database, FakeXService({user.id: posts}), sender, 60).poll_once()

    assert sender.messages == [(10, "101"), (10, "102"), (10, "103"), (20, "103")]


async def test_account_empty_at_follow_delivers_its_first_future_post(database, post_factory):
    user = XUser("x-1", "example")
    await database.ensure_tracked_user(user, None)
    await database.upsert_subscription(
        guild_id=1,
        channel_id=10,
        user=user,
        include_reposts=False,
        ping_role_id=None,
        start_after_post_id=None,
    )
    sender = RecordingSender()

    await Watcher(
        database,
        FakeXService({user.id: [post_factory("101")]}),
        sender,
        60,
    ).poll_once()

    assert sender.messages == [(10, "101")]


async def test_unexpected_x_failure_does_not_stop_other_accounts(database, post_factory):
    failing_user = XUser("x-1", "broken")
    healthy_user = XUser("x-2", "healthy")
    for user in (failing_user, healthy_user):
        await database.ensure_tracked_user(user, "100")
        await database.upsert_subscription(
            guild_id=1,
            channel_id=10,
            user=user,
            include_reposts=False,
            ping_role_id=None,
            start_after_post_id="100",
        )
    x_service = FakeXService(
        {
            failing_user.id: RuntimeError("unexpected parse failure"),
            healthy_user.id: [post_factory("101"), post_factory("100")],
        }
    )
    sender = RecordingSender()

    await Watcher(database, x_service, sender, 60).poll_once()

    assert x_service.fetches == {failing_user.id: 1, healthy_user.id: 1}
    assert sender.messages == [(10, "101")]
