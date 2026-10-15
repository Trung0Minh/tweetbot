from datetime import UTC, datetime, timedelta

import libsql
import pytest
import pytest_asyncio

from app.db import Database
from app.models import XPost


@pytest_asyncio.fixture(params=["sqlite", "turso"])
async def database(request, tmp_path, monkeypatch):
    path = tmp_path / "bot.db"
    if request.param == "turso":
        connect = libsql.connect

        def connect_remote(database, *, auth_token):
            assert database == "libsql://tweetbot-test.turso.io"
            assert auth_token == "test-token"
            return connect(str(path))

        monkeypatch.setattr(libsql, "connect", connect_remote)
        db = Database(
            tmp_path / "unused-local" / "bot.db",
            turso_database_url="libsql://tweetbot-test.turso.io",
            turso_auth_token="test-token",
        )
    else:
        db = Database(path)
    await db.connect()
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def post_factory():
    def build(
        post_id: str,
        *,
        username: str = "example",
        display_name: str = "Example Account",
        avatar_url: str | None = "https://pbs.twimg.com/profile_images/example.jpg",
        seconds: int | None = None,
        is_reply: bool = False,
        is_repost: bool = False,
        is_quote: bool = False,
    ) -> XPost:
        offset = int(post_id) if seconds is None and post_id.isdigit() else (seconds or 0)
        return XPost(
            id=post_id,
            username=username,
            display_name=display_name,
            avatar_url=avatar_url,
            created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=offset),
            is_reply=is_reply,
            is_repost=is_repost,
            is_quote=is_quote,
        )

    return build
