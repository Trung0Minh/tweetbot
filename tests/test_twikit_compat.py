from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup
from twikit import Client
from twikit.user import User

from app.twikit_compat import (
    PatchedClientTransaction,
    install_transaction_patch,
    install_user_parser_patch,
)


class FakeSession:
    def __init__(self, javascript: str) -> None:
        self.javascript = javascript
        self.requested_url = None

    async def request(self, *, method, url, headers):
        self.requested_url = url
        return SimpleNamespace(text=self.javascript)


class LoginFallbackSession:
    def __init__(self) -> None:
        self.requested_urls = []

    async def request(self, *, method, url, headers):
        self.requested_urls.append(url)
        if url == "https://x.com/i/flow/login":
            return SimpleNamespace(
                content=(b'<script>,59924:"ondemand.s",59924:"f481fbe"</script>')
            )
        return SimpleNamespace(text="const values = [39], 16 + [1], 16 + [16], 16;")


@pytest.mark.parametrize("quote", ['"', "'"])
async def test_current_x_chunk_format_resolves_key_byte_indices(quote):
    html = BeautifulSoup(
        f'<script>,1234:"ondemand.s",1234:{quote}abc123{quote}</script>',
        "html.parser",
    )
    session = FakeSession("const values = [2], 16 + [12], 16 + [42], 16;")

    row_index, key_indices = await PatchedClientTransaction().get_indices(
        html, session, {"User-Agent": "test"}
    )

    assert row_index == 2
    assert key_indices == [12, 42]
    assert session.requested_url == (
        "https://abs.twimg.com/responsive-web/client-web/ondemand.s.abc123a.js"
    )


async def test_new_logged_out_homepage_uses_login_page_for_bundle_discovery():
    homepage = BeautifulSoup(
        '<html><meta name="twitter-site-verification" content="key"></html>',
        "html.parser",
    )
    session = LoginFallbackSession()

    row_index, key_indices = await PatchedClientTransaction().get_indices(
        homepage, session, {"User-Agent": "test"}
    )

    assert row_index == 39
    assert key_indices == [1, 16]
    assert session.requested_urls == [
        "https://x.com/i/flow/login",
        "https://abs.twimg.com/responsive-web/client-web/ondemand.s.f481fbea.js",
    ]


async def test_patch_replaces_the_vulnerable_twikit_transaction_parser():
    client = Client(language="en-US")
    try:
        assert install_transaction_patch(client) is True
        assert isinstance(client.client_transaction, PatchedClientTransaction)
        assert install_transaction_patch(client) is False
    finally:
        await client.http.aclose()


def test_user_parser_accepts_description_without_urls():
    data = {
        "rest_id": "42",
        "is_blue_verified": False,
        "legacy": {
            "created_at": "Wed Aug 18 00:00:00 +0000 2026",
            "name": "Example",
            "screen_name": "example",
            "profile_image_url_https": "https://example.com/avatar.jpg",
            "location": "",
            "description": "No links here",
            "entities": {"description": {}},
        },
    }

    install_user_parser_patch()
    user = User(SimpleNamespace(), data)

    assert user.description_urls == []
    assert user.pinned_tweet_ids == []
    assert user.followers_count == 0
    assert data["legacy"]["entities"]["description"] == {}
