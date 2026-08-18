from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import Protocol

from app.db import Database
from app.models import Subscription, XPost, XUser
from app.x_service import XRateLimited, XService, XServiceError

logger = logging.getLogger(__name__)


class PostSender(Protocol):
    async def send(self, subscription: Subscription, post: XPost) -> None: ...


def posts_after_marker(posts: list[XPost], marker: str | None) -> list[XPost]:
    if marker is None:
        return sorted(posts, key=lambda post: (post.created_at, post.id))

    for index, post in enumerate(posts):
        if post.id == marker:
            return list(reversed(posts[:index]))

    if marker.isdigit() and all(post.id.isdigit() for post in posts):
        unseen = [post for post in posts if int(post.id) > int(marker)]
        return sorted(unseen, key=lambda post: (post.created_at, int(post.id)))

    logger.warning("Saved X post marker %s is absent from the recent timeline", marker)
    return []


def should_deliver(post: XPost, include_reposts: bool) -> bool:
    if post.is_reply:
        return False
    return not (post.is_repost and not include_reposts)


class Watcher:
    def __init__(
        self,
        database: Database,
        x_service: XService,
        sender: PostSender,
        poll_interval_seconds: int,
    ) -> None:
        self.database = database
        self.x_service = x_service
        self.sender = sender
        self.poll_interval_seconds = poll_interval_seconds
        self.last_successful_poll_at: datetime | None = None
        self._stop_event = asyncio.Event()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def run(self) -> None:
        self._running = True
        logger.info("Watcher started")
        try:
            while not self._stop_event.is_set():
                await self.poll_once()
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.poll_interval_seconds
                    )
        finally:
            self._running = False

    def stop(self) -> None:
        self._stop_event.set()

    async def poll_once(self) -> None:
        for tracked_user in await self.database.get_tracked_users():
            checked_at = datetime.now(UTC)
            logger.info("Polling @%s", tracked_user.x_username)
            try:
                posts = await self.x_service.get_recent_posts(tracked_user.x_user_id)
            except XRateLimited as exc:
                logger.warning(
                    "Rate limited while polling @%s; reset=%s",
                    tracked_user.x_username,
                    exc.reset_at,
                )
                await self.database.update_poll_state(
                    tracked_user.x_user_id,
                    last_seen_post_id=None,
                    checked_at=checked_at,
                    successful=False,
                )
                continue
            except XServiceError:
                logger.exception("X fetch failed for @%s", tracked_user.x_username)
                await self.database.update_poll_state(
                    tracked_user.x_user_id,
                    last_seen_post_id=None,
                    checked_at=checked_at,
                    successful=False,
                )
                continue
            except Exception:
                logger.exception("Unexpected X fetch failure for @%s", tracked_user.x_username)
                await self.database.update_poll_state(
                    tracked_user.x_user_id,
                    last_seen_post_id=None,
                    checked_at=checked_at,
                    successful=False,
                )
                continue

            if not posts:
                await self._record_success(tracked_user.x_user_id, None, checked_at)
                continue

            newest_post = posts[0]
            await self.database.ensure_tracked_user(
                XUser(tracked_user.x_user_id, newest_post.username), initial_post_id=None
            )
            new_posts = posts_after_marker(posts, tracked_user.last_seen_post_id)
            if new_posts:
                logger.info("Detected %d new posts for @%s", len(new_posts), newest_post.username)

            subscriptions = await self.database.get_subscriptions_for_user(tracked_user.x_user_id)
            for subscription in subscriptions:
                eligible_posts = posts_after_marker(posts, subscription.start_after_post_id)
                for post in eligible_posts:
                    if not should_deliver(post, subscription.include_reposts):
                        continue
                    if await self.database.was_sent(subscription.id, post.id):
                        continue
                    try:
                        await self.sender.send(subscription, post)
                    except Exception:
                        logger.exception(
                            "Failed sending post %s to guild=%s channel=%s",
                            post.id,
                            subscription.guild_id,
                            subscription.channel_id,
                        )
                        continue
                    await self.database.mark_sent(subscription.id, post.id)
                    logger.info(
                        "Sent post %s to guild=%s channel=%s",
                        post.id,
                        subscription.guild_id,
                        subscription.channel_id,
                    )

            await self._record_success(tracked_user.x_user_id, newest_post.id, checked_at)

    async def _record_success(
        self, x_user_id: str, last_seen_post_id: str | None, checked_at: datetime
    ) -> None:
        await self.database.update_poll_state(
            x_user_id,
            last_seen_post_id=last_seen_post_id,
            checked_at=checked_at,
            successful=True,
        )
        self.last_successful_poll_at = checked_at
