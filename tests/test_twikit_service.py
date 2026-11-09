import traceback
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from twikit.errors import Forbidden, TooManyRequests, UserNotFound

from app.twikit_service import TwikitXService
from app.x_service import XRateLimited, XServiceError, XUserNotFound


class FakeClient:
    def __init__(self) -> None:
        self.login_kwargs = None
        self.timeline_args = None
        self.loaded_cookies = None
        self.saved_cookies = None
        self.cookies = None

    async def login(self, **kwargs):
        self.login_kwargs = kwargs

    def load_cookies(self, path):
        self.loaded_cookies = path

    def save_cookies(self, path):
        self.saved_cookies = path

    def set_cookies(self, cookies):
        self.cookies = cookies

    async def get_user_by_screen_name(self, username):
        return SimpleNamespace(id="42", screen_name=username.title())

    async def get_user_tweets(self, user_id, tweet_type, count):
        self.timeline_args = (user_id, tweet_type, count)
        return [
            SimpleNamespace(
                id="101",
                user=SimpleNamespace(
                    screen_name="Example",
                    name="Example Account",
                    profile_image_url="https://pbs.twimg.com/profile_images/example.jpg",
                ),
                created_at_datetime=datetime(2026, 1, 1, tzinfo=UTC),
                in_reply_to=None,
                retweeted_tweet=None,
                full_text="quoted",
                _data={"legacy": {"is_quote_status": True}},
            ),
            SimpleNamespace(
                id="100",
                user=SimpleNamespace(
                    screen_name="Example",
                    name="Example Account",
                    profile_image_url="https://pbs.twimg.com/profile_images/example.jpg",
                ),
                created_at_datetime=datetime(2025, 12, 31, tzinfo=UTC),
                in_reply_to="99",
                retweeted_tweet=object(),
                full_text="reply",
                _data={"legacy": {"is_quote_status": False}},
            ),
        ]


async def test_authenticate_uses_twikit_cookie_file(tmp_path):
    client = FakeClient()
    cookies_path = tmp_path / "session" / "cookies.json"
    service = TwikitXService(
        username="bot",
        email="bot@example.com",
        password="secret",
        cookies_path=cookies_path,
        client=client,
    )

    await service.authenticate()

    assert cookies_path.parent.is_dir()
    assert client.login_kwargs == {
        "auth_info_1": "bot",
        "auth_info_2": "bot@example.com",
        "password": "secret",
    }
    assert client.saved_cookies == str(cookies_path)


async def test_authenticate_reuses_valid_saved_cookies(tmp_path):
    client = FakeClient()
    cookies_path = tmp_path / "cookies.json"
    cookies_path.write_text("{}", encoding="utf-8")
    service = TwikitXService("bot", "bot@example.com", "secret", cookies_path, client)

    await service.authenticate()

    assert client.loaded_cookies == str(cookies_path)
    assert client.login_kwargs is None


async def test_authenticate_uses_environment_cookies_without_password_login(tmp_path):
    client = FakeClient()
    cookies = {"auth_token": "auth-token", "ct0": "csrf-token"}
    cookies_path = tmp_path / "cookies.json"
    service = TwikitXService(
        "bot",
        "",
        "",
        cookies_path,
        client,
        auth_cookies=cookies,
    )

    await service.authenticate()

    assert client.cookies == cookies
    assert client.login_kwargs is None
    assert client.saved_cookies == str(cookies_path)


async def test_authenticate_keeps_valid_environment_cookies_if_saving_fails(tmp_path):
    class ReadOnlyCookieClient(FakeClient):
        def save_cookies(self, path):
            raise OSError("read-only filesystem")

    service = TwikitXService(
        "bot",
        "",
        "",
        tmp_path / "cookies.json",
        ReadOnlyCookieClient(),
        auth_cookies={"auth_token": "auth-token", "ct0": "csrf-token"},
    )

    await service.authenticate()


async def test_rejected_environment_cookies_do_not_fall_back_to_password_login(tmp_path):
    class RejectedCookieClient(FakeClient):
        async def get_user_by_screen_name(self, username):
            raise Forbidden("SENSITIVE COOKIE VALIDATION RESPONSE")

    client = RejectedCookieClient()
    service = TwikitXService(
        "bot",
        "bot@example.com",
        "secret",
        tmp_path / "cookies.json",
        client,
        auth_cookies={"auth_token": "auth-token", "ct0": "csrf-token"},
    )

    with pytest.raises(XServiceError, match="Browser X cookies were rejected"):
        await service.authenticate()

    assert client.login_kwargs is None


async def test_authenticate_hides_forbidden_response_body(tmp_path):
    class BlockedClient(FakeClient):
        async def login(self, **kwargs):
            raise Forbidden("SENSITIVE CLOUDFLARE RESPONSE BODY")

    service = TwikitXService(
        "bot", "bot@example.com", "secret", tmp_path / "cookies", BlockedClient()
    )

    with pytest.raises(XServiceError) as caught:
        await service.authenticate()

    formatted = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert str(caught.value) == ("X blocked credential login; use a browser-created cookie session")
    assert "SENSITIVE CLOUDFLARE RESPONSE BODY" not in formatted


async def test_twikit_objects_are_normalized_to_internal_models(tmp_path):
    client = FakeClient()
    service = TwikitXService("bot", "bot@example.com", "secret", tmp_path / "cookies", client)

    user = await service.resolve_user("example")
    posts = await service.get_recent_posts(user.id)

    assert user.id == "42"
    assert user.username == "Example"
    assert client.timeline_args == ("42", "Tweets", 40)
    assert posts[0].is_quote is True
    assert posts[0].is_reply is False
    assert posts[0].is_repost is False
    assert posts[0].display_name == "Example Account"
    assert posts[0].avatar_url == "https://pbs.twimg.com/profile_images/example.jpg"
    assert posts[0].url == "https://twitter.com/Example/status/101"
    assert posts[1].is_reply is True
    assert posts[1].is_repost is True


async def test_user_not_found_is_exposed_as_clean_adapter_error(tmp_path):
    class MissingClient(FakeClient):
        async def get_user_by_screen_name(self, username):
            raise UserNotFound(username)

    service = TwikitXService(
        "bot", "bot@example.com", "secret", tmp_path / "cookies", MissingClient()
    )

    with pytest.raises(XUserNotFound):
        await service.resolve_user("missing")


async def test_rate_limit_is_exposed_as_clean_adapter_error(tmp_path):
    class LimitedClient(FakeClient):
        async def get_user_tweets(self, user_id, tweet_type, count):
            raise TooManyRequests("slow down", headers={"x-rate-limit-reset": "123"})

    service = TwikitXService(
        "bot", "bot@example.com", "secret", tmp_path / "cookies", LimitedClient()
    )

    with pytest.raises(XRateLimited) as caught:
        await service.get_recent_posts("42")

    assert caught.value.reset_at == 123


def _tweet(legacy, *, full_text="hello", **data):
    return SimpleNamespace(
        id="1",
        user=SimpleNamespace(screen_name="Example", name="Example", profile_image_url=None),
        created_at_datetime=datetime(2026, 1, 1, tzinfo=UTC),
        in_reply_to=None,
        retweeted_tweet=None,
        full_text=full_text,
        _data={"legacy": legacy, **data},
    )


async def _parse(tmp_path, tweet):
    class OneTweetClient(FakeClient):
        async def get_user_tweets(self, user_id, tweet_type, count):
            return [tweet]

    service = TwikitXService("bot", "e", "p", tmp_path / "c", OneTweetClient())
    return (await service.get_recent_posts("42"))[0]


async def test_all_photos_come_from_extended_entities(tmp_path):
    photos = [
        {
            "type": "photo",
            "media_url_https": f"https://pbs.twimg.com/media/{i}.jpg",
            "url": "https://t.co/m",
        }
        for i in range(4)
    ]
    legacy = {"entities": {"media": photos[:1]}, "extended_entities": {"media": photos}}
    post = await _parse(tmp_path, _tweet(legacy, full_text="look https://t.co/m"))

    assert post.image_urls == tuple(
        f"https://pbs.twimg.com/media/{i}.jpg?name=large" for i in range(4)
    )
    assert post.has_video is False
    assert post.text == "look"


async def test_entities_media_is_fallback(tmp_path):
    photo = {
        "type": "photo",
        "media_url_https": "https://pbs.twimg.com/media/a.jpg",
        "url": "https://t.co/m",
    }
    post = await _parse(tmp_path, _tweet({"entities": {"media": [photo]}}))

    assert post.image_urls == ("https://pbs.twimg.com/media/a.jpg?name=large",)


async def test_video_and_gif_set_has_video(tmp_path):
    for kind in ("video", "animated_gif"):
        item = {
            "type": kind,
            "media_url_https": "https://pbs.twimg.com/x.jpg",
            "url": "https://t.co/v",
        }
        post = await _parse(tmp_path, _tweet({"extended_entities": {"media": [item]}}))
        assert post.has_video is True
        assert post.image_urls == ()


async def test_text_unescapes_entities_and_keeps_other_links(tmp_path):
    post = await _parse(tmp_path, _tweet({}, full_text="a &amp; b https://t.co/other"))

    assert post.text == "a & b https://t.co/other"


async def test_tco_links_are_expanded_but_media_links_removed(tmp_path):
    legacy = {
        "entities": {
            "urls": [{"url": "https://t.co/abc", "expanded_url": "https://example.com/page"}],
            "media": [
                {"type": "photo", "media_url_https": "https://x/a.jpg", "url": "https://t.co/m"}
            ],
        },
    }
    post = await _parse(tmp_path, _tweet(legacy, full_text="see https://t.co/abc https://t.co/m"))

    assert post.text == "see https://example.com/page"


async def test_malformed_url_entity_leaves_text_unchanged(tmp_path):
    legacy = {"entities": {"urls": [{"url": "https://t.co/abc"}]}}
    post = await _parse(tmp_path, _tweet(legacy, full_text="see https://t.co/abc"))

    assert post.text == "see https://t.co/abc"


async def test_note_tweet_links_are_expanded(tmp_path):
    from twikit.tweet import Tweet

    entity_set = {"urls": [{"url": "https://t.co/abc", "expanded_url": "https://example.com/x"}]}
    data = {
        "rest_id": "1",
        "legacy": {"full_text": "short\u2026", "created_at": "Thu Jan 01 00:00:00 +0000 2026"},
        "note_tweet": {
            "note_tweet_results": {
                "result": {"text": "long https://t.co/abc", "entity_set": entity_set}
            }
        },
    }
    tweet = Tweet(None, data, None)
    tweet.user = SimpleNamespace(screen_name="Example", name="Example", profile_image_url=None)
    post = await _parse(tmp_path, tweet)

    assert post.text == "long https://example.com/x"


async def test_note_tweet_text_is_used(tmp_path):
    from twikit.tweet import Tweet

    note = "long note text " * 50
    data = {
        "rest_id": "1",
        "legacy": {
            "full_text": "short\u2026",
            "created_at": "Thu Jan 01 00:00:00 +0000 2026",
        },
        "note_tweet": {"note_tweet_results": {"result": {"text": note, "entity_set": {}}}},
    }
    tweet = Tweet(None, data, None)
    tweet.user = SimpleNamespace(screen_name="Example", name="Example", profile_image_url=None)
    post = await _parse(tmp_path, tweet)

    assert post.text == note.strip()


async def test_quote_detection_uses_legacy_flag_or_quoted_result(tmp_path):
    assert (await _parse(tmp_path, _tweet({"is_quote_status": False}))).is_quote is False
    assert (await _parse(tmp_path, _tweet({"is_quote_status": True}))).is_quote is True
    quoted = _tweet({}, quoted_status_result={"result": {}})
    assert (await _parse(tmp_path, quoted)).is_quote is True


async def test_malformed_media_is_treated_as_no_media(tmp_path):
    bad = {"type": "photo"}
    post = await _parse(tmp_path, _tweet({"extended_entities": {"media": [bad]}}))

    assert post.image_urls == ()
    assert post.has_video is False
