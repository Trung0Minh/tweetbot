from datetime import UTC, datetime, timedelta

import pytest

from app.models import XPost


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
