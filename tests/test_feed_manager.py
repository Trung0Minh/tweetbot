import pytest
import pytest_asyncio

from app.db import Database
from app.feed_manager import FeedManager, parse_usernames
from app.models import XUser
from app.x_service import XUserNotFound


class FakeXService:
    def __init__(self):
        self.users = {
            "foo": XUser("1", "Foo"),
            "bar": XUser("2", "Bar"),
        }
        self.posts = {"1": [], "2": []}

    async def resolve_user(self, username):
        try:
            return self.users[username.casefold()]
        except KeyError as exc:
            raise XUserNotFound(username) from exc

    async def get_recent_posts(self, user_id):
        return self.posts[user_id]


@pytest_asyncio.fixture
async def database(tmp_path):
    db = Database(tmp_path / "bot.db")
    await db.connect()
    await db.initialize()
    yield db
    await db.close()


def test_username_parser_normalizes_and_deduplicates_handles():
    assert parse_usernames(" @foo, bar,@baz,FOO ") == ["foo", "bar", "baz"]


def test_username_parser_rejects_empty_entries():
    with pytest.raises(ValueError, match="empty"):
        parse_usernames("foo, ,bar")


async def test_follow_uses_partial_success_and_sets_current_post_boundary(database, post_factory):
    x_service = FakeXService()
    x_service.posts["1"] = [post_factory("100", username="Foo")]
    manager = FeedManager(database, x_service)

    results = await manager.follow_many(
        usernames=["foo", "missing"],
        guild_id=1,
        channel_id=2,
        include_reposts=False,
        ping_role_id=None,
    )

    assert [result.success for result in results] == [True, False]
    subscription = (await database.list_subscriptions(1))[0]
    assert subscription.start_after_post_id == "100"
    assert (await database.get_tracked_users())[0].last_seen_post_id == "100"


async def test_follow_again_updates_without_resetting_boundary(database, post_factory):
    x_service = FakeXService()
    x_service.posts["1"] = [post_factory("100", username="Foo")]
    manager = FeedManager(database, x_service)
    await manager.follow_many(["foo"], 1, 2, False, None)
    x_service.posts["1"] = [post_factory("200", username="Foo")]

    results = await manager.follow_many(["foo"], 1, 2, True, 3)

    subscription = (await database.list_subscriptions(1))[0]
    assert results[0].action == "updated"
    assert subscription.start_after_post_id == "100"
    assert subscription.include_reposts is True


async def test_new_channel_requires_safe_boundary_for_already_tracked_account(
    database, post_factory
):
    x_service = FakeXService()
    x_service.posts["1"] = [post_factory("100", username="Foo")]
    manager = FeedManager(database, x_service)
    await manager.follow_many(["foo"], 1, 2, False, None)
    x_service.posts["1"] = []

    results = await manager.follow_many(["foo"], 1, 3, False, None)

    assert results[0].success is False
    assert await database.list_subscriptions(1, 3) == []
