from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Any

from twikit import Client
from twikit.errors import (
    Forbidden,
    NotFound,
    TooManyRequests,
    TwitterException,
    Unauthorized,
    UserNotFound,
)

from app.models import XPost, XUser
from app.twikit_compat import install_transaction_patch, install_user_parser_patch
from app.x_service import XRateLimited, XServiceError, XUserNotFound

logger = logging.getLogger(__name__)


def _is_quote(tweet: Any) -> bool:
    # The legacy flag can be absent; also check quoted_status_result.
    data = tweet._data
    return bool(data.get("legacy", {}).get("is_quote_status") or data.get("quoted_status_result"))


def _text_and_media(tweet: Any) -> dict[str, Any]:
    """Extract post text, photo URLs and video presence; malformed media counts as no media."""
    try:
        text = tweet.full_text or ""
    except (AttributeError, KeyError, TypeError):
        text = tweet._data["legacy"].get("full_text", "")
    image_urls: list[str] = []
    has_video = False
    try:
        legacy = tweet._data["legacy"]
        media = legacy.get("extended_entities", {}).get("media") or legacy.get("entities", {}).get(
            "media", []
        )
        urls: list[str] = []
        video = False
        for item in media:
            if item.get("url"):
                text = text.replace(item["url"], "")
            if item.get("type") == "photo":
                urls.append(f"{item['media_url_https']}?name=large")
            elif item.get("type") in ("video", "animated_gif"):
                video = True
        image_urls, has_video = urls, video
    except (AttributeError, KeyError, TypeError):
        pass
    return {
        "text": html.unescape(text).strip(),
        "image_urls": tuple(image_urls),
        "has_video": has_video,
    }


class TwikitXService:
    def __init__(
        self,
        username: str,
        email: str,
        password: str,
        cookies_path: Path,
        client: Any | None = None,
        auth_cookies: dict[str, str] | None = None,
    ) -> None:
        self.username = username
        self.email = email
        self.password = password
        self.cookies_path = cookies_path
        self.auth_cookies = auth_cookies
        self.client = client or Client(language="en-US")
        install_transaction_patch(self.client)
        install_user_parser_patch()

    async def authenticate(self) -> None:
        if self.auth_cookies is not None:
            try:
                self.client.set_cookies(self.auth_cookies)
                await self.client.get_user_by_screen_name(self.username)
            except TooManyRequests as exc:
                raise self._rate_limit_error(exc) from exc
            except (OSError, ValueError, Unauthorized, Forbidden):
                raise XServiceError("Browser X cookies were rejected") from None
            except Exception:
                raise XServiceError("Could not validate browser X cookies") from None
            try:
                self.cookies_path.parent.mkdir(parents=True, exist_ok=True)
                self.client.save_cookies(str(self.cookies_path))
            except OSError:
                logger.warning("Could not save the local X cookie file")
            return

        self.cookies_path.parent.mkdir(parents=True, exist_ok=True)
        if self.cookies_path.exists():
            try:
                self.client.load_cookies(str(self.cookies_path))
                await self.client.get_user_by_screen_name(self.username)
                return
            except (OSError, ValueError, Unauthorized, Forbidden):
                logger.warning("Saved X session is invalid; using credential login")
            except TooManyRequests as exc:
                raise self._rate_limit_error(exc) from exc
            except TwitterException as exc:
                raise XServiceError("Could not validate the saved X session") from exc

        try:
            await self.client.login(
                auth_info_1=self.username,
                auth_info_2=self.email,
                password=self.password,
            )
            self.client.save_cookies(str(self.cookies_path))
        except Forbidden:
            raise XServiceError(
                "X blocked credential login; use a browser-created cookie session"
            ) from None
        except TooManyRequests as exc:
            raise self._rate_limit_error(exc) from exc
        except TwitterException as exc:
            raise XServiceError("X authentication failed") from exc
        except Exception as exc:
            raise XServiceError("X authentication failed unexpectedly") from exc

    async def resolve_user(self, username: str) -> XUser:
        try:
            user = await self.client.get_user_by_screen_name(username)
        except (UserNotFound, NotFound) as exc:
            raise XUserNotFound(f"@{username} was not found") from exc
        except TooManyRequests as exc:
            raise self._rate_limit_error(exc) from exc
        except TwitterException as exc:
            raise XServiceError(f"Could not resolve @{username}") from exc
        except Exception as exc:
            raise XServiceError(f"Could not resolve @{username}") from exc
        try:
            return XUser(id=str(user.id), username=user.screen_name)
        except (AttributeError, KeyError, TypeError) as exc:
            raise XServiceError(f"X returned invalid user data for @{username}") from exc

    async def get_recent_posts(self, user_id: str) -> list[XPost]:
        try:
            tweets = await self.client.get_user_tweets(user_id, "Tweets", count=40)
        except TooManyRequests as exc:
            raise self._rate_limit_error(exc) from exc
        except TwitterException as exc:
            raise XServiceError(f"Could not fetch X user {user_id}") from exc
        except Exception as exc:
            raise XServiceError(f"Could not fetch X user {user_id}") from exc

        try:
            return [
                XPost(
                    id=str(tweet.id),
                    username=tweet.user.screen_name,
                    display_name=tweet.user.name,
                    avatar_url=tweet.user.profile_image_url,
                    created_at=tweet.created_at_datetime,
                    is_reply=tweet.in_reply_to is not None,
                    is_repost=tweet.retweeted_tweet is not None,
                    is_quote=_is_quote(tweet),
                    **_text_and_media(tweet),
                )
                for tweet in tweets
            ]
        except (AttributeError, KeyError, TypeError) as exc:
            raise XServiceError(f"X returned invalid timeline data for user {user_id}") from exc

    async def close(self) -> None:
        http_client = getattr(self.client, "http", None)
        close = getattr(http_client, "aclose", None)
        if close is not None:
            await close()

    @staticmethod
    def _rate_limit_error(exc: TooManyRequests) -> XRateLimited:
        return XRateLimited("X rate limit reached", getattr(exc, "rate_limit_reset", None))
