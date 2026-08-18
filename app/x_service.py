from __future__ import annotations

from typing import Protocol

from app.models import XPost, XUser


class XServiceError(Exception):
    """Base error exposed by the X integration boundary."""


class XUserNotFound(XServiceError):
    pass


class XRateLimited(XServiceError):
    def __init__(self, message: str, reset_at: int | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class XService(Protocol):
    async def authenticate(self) -> None: ...

    async def resolve_user(self, username: str) -> XUser: ...

    async def get_recent_posts(self, user_id: str) -> list[XPost]: ...

    async def close(self) -> None: ...
