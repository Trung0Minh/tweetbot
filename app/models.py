from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class XUser:
    id: str
    username: str


@dataclass(frozen=True, slots=True)
class XPost:
    id: str
    username: str
    display_name: str
    avatar_url: str | None
    created_at: datetime
    is_reply: bool = False
    is_repost: bool = False
    is_quote: bool = False

    @property
    def url(self) -> str:
        return f"https://twitter.com/{self.username}/status/{self.id}"


@dataclass(frozen=True, slots=True)
class Subscription:
    id: int
    guild_id: int
    channel_id: int
    x_user_id: str
    x_username: str
    include_reposts: bool
    ping_role_id: int | None
    start_after_post_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SubscriptionUpsertResult:
    subscription: Subscription
    created: bool


@dataclass(frozen=True, slots=True)
class TrackedUser:
    x_user_id: str
    x_username: str
    last_seen_post_id: str | None
    last_checked_at: datetime | None
    last_successful_poll_at: datetime | None
