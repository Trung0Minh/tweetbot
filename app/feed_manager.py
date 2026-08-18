from __future__ import annotations

from dataclasses import dataclass

from app.db import Database
from app.x_service import XService, XServiceError


def parse_usernames(value: str) -> list[str]:
    parts = value.split(",")
    if any(not part.strip().lstrip("@").strip() for part in parts):
        raise ValueError("feed-source contains an empty username")

    usernames: list[str] = []
    seen: set[str] = set()
    for part in parts:
        username = part.strip().lstrip("@").strip()
        key = username.casefold()
        if key not in seen:
            seen.add(key)
            usernames.append(username)
    return usernames


@dataclass(frozen=True, slots=True)
class FeedActionResult:
    username: str
    success: bool
    action: str
    error: str | None = None


class FeedManager:
    def __init__(self, database: Database, x_service: XService) -> None:
        self.database = database
        self.x_service = x_service

    async def follow_many(
        self,
        usernames: list[str],
        guild_id: int,
        channel_id: int,
        include_reposts: bool,
        ping_role_id: int | None,
    ) -> list[FeedActionResult]:
        results: list[FeedActionResult] = []
        for requested_username in usernames:
            try:
                user = await self.x_service.resolve_user(requested_username)
                existing = await self.database.get_subscription(guild_id, channel_id, user.id)
                if existing is None:
                    already_tracked = await self.database.get_tracked_user(user.id)
                    posts = await self.x_service.get_recent_posts(user.id)
                    if already_tracked is not None and not posts:
                        raise XServiceError("could not establish a safe current-post boundary")
                    boundary = posts[0].id if posts else None
                    await self.database.ensure_tracked_user(user, boundary)
                else:
                    boundary = existing.start_after_post_id
                    await self.database.ensure_tracked_user(user, initial_post_id=None)

                upserted = await self.database.upsert_subscription(
                    guild_id=guild_id,
                    channel_id=channel_id,
                    user=user,
                    include_reposts=include_reposts,
                    ping_role_id=ping_role_id,
                    start_after_post_id=boundary,
                )
                results.append(
                    FeedActionResult(
                        username=user.username,
                        success=True,
                        action="added" if upserted.created else "updated",
                    )
                )
            except XServiceError as exc:
                results.append(
                    FeedActionResult(
                        username=requested_username,
                        success=False,
                        action="failed",
                        error=str(exc),
                    )
                )
        return results

    async def unfollow_many(
        self, usernames: list[str], guild_id: int, channel_id: int
    ) -> list[FeedActionResult]:
        results: list[FeedActionResult] = []
        for username in usernames:
            removed = await self.database.remove_subscription(guild_id, channel_id, username)
            results.append(
                FeedActionResult(
                    username=username,
                    success=removed,
                    action="removed" if removed else "not followed",
                    error=None if removed else "not followed in this channel",
                )
            )
        return results
