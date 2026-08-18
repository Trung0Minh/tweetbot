from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.models import Subscription, SubscriptionUpsertResult, TrackedUser, XUser


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA foreign_keys = ON")
        await self._connection.execute("PRAGMA journal_mode = WAL")

    async def initialize(self) -> None:
        connection = self._require_connection()
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tracked_users (
                x_user_id TEXT PRIMARY KEY,
                x_username TEXT NOT NULL,
                last_seen_post_id TEXT,
                last_checked_at TEXT,
                last_successful_poll_at TEXT
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                x_user_id TEXT NOT NULL,
                x_username TEXT NOT NULL,
                include_reposts INTEGER NOT NULL DEFAULT 0,
                ping_role_id INTEGER,
                start_after_post_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(guild_id, channel_id, x_user_id),
                FOREIGN KEY(x_user_id) REFERENCES tracked_users(x_user_id)
            );

            CREATE TABLE IF NOT EXISTS sent_posts (
                subscription_id INTEGER NOT NULL,
                post_id TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY(subscription_id, post_id),
                FOREIGN KEY(subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE
            );
            """
        )
        await connection.commit()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def ensure_tracked_user(self, user: XUser, initial_post_id: str | None) -> bool:
        connection = self._require_connection()
        existing = await connection.execute_fetchall(
            "SELECT 1 FROM tracked_users WHERE x_user_id = ?", (user.id,)
        )
        created = not existing
        await connection.execute(
            """
            INSERT INTO tracked_users (x_user_id, x_username, last_seen_post_id)
            VALUES (?, ?, ?)
            ON CONFLICT(x_user_id) DO UPDATE SET x_username = excluded.x_username
            """,
            (user.id, user.username, initial_post_id),
        )
        await connection.execute(
            "UPDATE subscriptions SET x_username = ? WHERE x_user_id = ?",
            (user.username, user.id),
        )
        await connection.commit()
        return created

    async def upsert_subscription(
        self,
        *,
        guild_id: int,
        channel_id: int,
        user: XUser,
        include_reposts: bool,
        ping_role_id: int | None,
        start_after_post_id: str | None,
    ) -> SubscriptionUpsertResult:
        connection = self._require_connection()
        existing = await connection.execute_fetchall(
            """
            SELECT id FROM subscriptions
            WHERE guild_id = ? AND channel_id = ? AND x_user_id = ?
            """,
            (guild_id, channel_id, user.id),
        )
        created = not existing
        timestamp = _now().isoformat()
        await connection.execute(
            """
            INSERT INTO subscriptions (
                guild_id, channel_id, x_user_id, x_username, include_reposts,
                ping_role_id, start_after_post_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, channel_id, x_user_id) DO UPDATE SET
                x_username = excluded.x_username,
                include_reposts = excluded.include_reposts,
                ping_role_id = excluded.ping_role_id,
                updated_at = excluded.updated_at
            """,
            (
                guild_id,
                channel_id,
                user.id,
                user.username,
                int(include_reposts),
                ping_role_id,
                start_after_post_id,
                timestamp,
                timestamp,
            ),
        )
        await connection.commit()
        subscription = await self.get_subscription(guild_id, channel_id, user.id)
        if subscription is None:
            raise RuntimeError("Subscription upsert did not produce a row")
        return SubscriptionUpsertResult(subscription=subscription, created=created)

    async def get_subscription(
        self, guild_id: int, channel_id: int, x_user_id: str
    ) -> Subscription | None:
        cursor = await self._require_connection().execute(
            """
            SELECT * FROM subscriptions
            WHERE guild_id = ? AND channel_id = ? AND x_user_id = ?
            """,
            (guild_id, channel_id, x_user_id),
        )
        row = await cursor.fetchone()
        return self._subscription_from_row(row) if row else None

    async def get_subscriptions_for_user(self, x_user_id: str) -> list[Subscription]:
        cursor = await self._require_connection().execute(
            "SELECT * FROM subscriptions WHERE x_user_id = ? ORDER BY id", (x_user_id,)
        )
        return [self._subscription_from_row(row) for row in await cursor.fetchall()]

    async def list_subscriptions(
        self, guild_id: int, channel_id: int | None = None
    ) -> list[Subscription]:
        connection = self._require_connection()
        if channel_id is None:
            cursor = await connection.execute(
                "SELECT * FROM subscriptions WHERE guild_id = ? ORDER BY channel_id, x_username",
                (guild_id,),
            )
        else:
            cursor = await connection.execute(
                """
                SELECT * FROM subscriptions
                WHERE guild_id = ? AND channel_id = ? ORDER BY x_username
                """,
                (guild_id, channel_id),
            )
        return [self._subscription_from_row(row) for row in await cursor.fetchall()]

    async def remove_subscription(self, guild_id: int, channel_id: int, username: str) -> bool:
        connection = self._require_connection()
        cursor = await connection.execute(
            """
            SELECT x_user_id FROM subscriptions
            WHERE guild_id = ? AND channel_id = ? AND x_username = ? COLLATE NOCASE
            """,
            (guild_id, channel_id, username),
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        x_user_id = row["x_user_id"]
        await connection.execute(
            """
            DELETE FROM subscriptions
            WHERE guild_id = ? AND channel_id = ? AND x_user_id = ?
            """,
            (guild_id, channel_id, x_user_id),
        )
        await connection.execute(
            """
            DELETE FROM tracked_users
            WHERE x_user_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM subscriptions
                  WHERE subscriptions.x_user_id = tracked_users.x_user_id
              )
            """,
            (x_user_id,),
        )
        await connection.commit()
        return True

    async def get_tracked_users(self) -> list[TrackedUser]:
        cursor = await self._require_connection().execute(
            "SELECT * FROM tracked_users ORDER BY x_username"
        )
        return [self._tracked_user_from_row(row) for row in await cursor.fetchall()]

    async def get_tracked_user(self, x_user_id: str) -> TrackedUser | None:
        cursor = await self._require_connection().execute(
            "SELECT * FROM tracked_users WHERE x_user_id = ?", (x_user_id,)
        )
        row = await cursor.fetchone()
        return self._tracked_user_from_row(row) if row else None

    async def update_poll_state(
        self,
        x_user_id: str,
        *,
        last_seen_post_id: str | None,
        checked_at: datetime,
        successful: bool,
    ) -> None:
        connection = self._require_connection()
        successful_at = checked_at.isoformat() if successful else None
        await connection.execute(
            """
            UPDATE tracked_users SET
                last_seen_post_id = COALESCE(?, last_seen_post_id),
                last_checked_at = ?,
                last_successful_poll_at = COALESCE(?, last_successful_poll_at)
            WHERE x_user_id = ?
            """,
            (last_seen_post_id, checked_at.isoformat(), successful_at, x_user_id),
        )
        await connection.commit()

    async def was_sent(self, subscription_id: int, post_id: str) -> bool:
        cursor = await self._require_connection().execute(
            "SELECT 1 FROM sent_posts WHERE subscription_id = ? AND post_id = ?",
            (subscription_id, post_id),
        )
        return await cursor.fetchone() is not None

    async def mark_sent(self, subscription_id: int, post_id: str) -> bool:
        connection = self._require_connection()
        cursor = await connection.execute(
            """
            INSERT OR IGNORE INTO sent_posts (subscription_id, post_id, sent_at)
            VALUES (?, ?, ?)
            """,
            (subscription_id, post_id, _now().isoformat()),
        )
        await connection.commit()
        return cursor.rowcount == 1

    async def counts(self) -> tuple[int, int]:
        connection = self._require_connection()
        tracked = await connection.execute_fetchall("SELECT COUNT(*) AS count FROM tracked_users")
        subscriptions = await connection.execute_fetchall(
            "SELECT COUNT(*) AS count FROM subscriptions"
        )
        return tracked[0]["count"], subscriptions[0]["count"]

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database is not connected")
        return self._connection

    @staticmethod
    def _subscription_from_row(row: aiosqlite.Row) -> Subscription:
        return Subscription(
            id=row["id"],
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            x_user_id=row["x_user_id"],
            x_username=row["x_username"],
            include_reposts=bool(row["include_reposts"]),
            ping_role_id=row["ping_role_id"],
            start_after_post_id=row["start_after_post_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _tracked_user_from_row(row: aiosqlite.Row) -> TrackedUser:
        return TrackedUser(
            x_user_id=row["x_user_id"],
            x_username=row["x_username"],
            last_seen_post_id=row["last_seen_post_id"],
            last_checked_at=_parse_datetime(row["last_checked_at"]),
            last_successful_poll_at=_parse_datetime(row["last_successful_poll_at"]),
        )
