from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup
from twikit.user import User as TwikitUser
from twikit.x_client_transaction import transaction as upstream_transaction
from twikit.x_client_transaction.transaction import ClientTransaction

logger = logging.getLogger(__name__)

# Temporary compatibility for X's 2026 webpack format change.
# Source: https://github.com/d60/twikit/pull/432
_ON_DEMAND_CHUNK_PATTERN = re.compile(r',(\d+):["\']ondemand\.s["\']')
_ON_DEMAND_HASH_PATTERN = r',{}:["\']([0-9a-f]+)["\']'
_KEY_BYTE_INDEX_PATTERN = re.compile(r"\[(\d+)\],\s*16")
_ORIGINAL_USER_INIT = TwikitUser.__init__


def _patched_user_init(self: TwikitUser, client: Any, data: dict) -> None:
    normalized_data = data.copy()
    normalized_data.setdefault("is_blue_verified", False)
    legacy = data["legacy"].copy()
    legacy.setdefault("pinned_tweet_ids_str", [])
    legacy.setdefault("verified", False)
    legacy.setdefault("possibly_sensitive", False)
    legacy.setdefault("can_dm", False)
    legacy.setdefault("can_media_tag", False)
    legacy.setdefault("want_retweets", False)
    legacy.setdefault("default_profile", False)
    legacy.setdefault("default_profile_image", False)
    legacy.setdefault("has_custom_timelines", False)
    legacy.setdefault("followers_count", 0)
    legacy.setdefault("fast_followers_count", 0)
    legacy.setdefault("normal_followers_count", 0)
    legacy.setdefault("friends_count", 0)
    legacy.setdefault("favourites_count", 0)
    legacy.setdefault("listed_count", 0)
    legacy.setdefault("media_count", 0)
    legacy.setdefault("statuses_count", 0)
    legacy.setdefault("is_translator", False)
    legacy.setdefault("translator_type", "none")
    legacy.setdefault("withheld_in_countries", [])
    entities = legacy.get("entities", {}).copy()
    description = entities.get("description", {}).copy()
    description.setdefault("urls", [])
    entities["description"] = description
    legacy["entities"] = entities
    normalized_data["legacy"] = legacy
    _ORIGINAL_USER_INIT(self, client, normalized_data)


_patched_user_init._tweetbot_compat = True  # type: ignore[attr-defined]


class PatchedClientTransaction(ClientTransaction):
    async def get_indices(self, home_page_response, session, headers):
        response = self.validate_response(home_page_response) or self.home_page_response
        page_source = str(response)
        key_byte_indices: list[int] = []

        chunk_match = _ON_DEMAND_CHUNK_PATTERN.search(page_source)
        if chunk_match is None:
            login_page = await session.request(
                method="GET", url="https://x.com/i/flow/login", headers=headers
            )
            page_source = str(BeautifulSoup(login_page.content, "lxml"))
            chunk_match = _ON_DEMAND_CHUNK_PATTERN.search(page_source)
        if chunk_match:
            chunk_index = chunk_match.group(1)
            hash_match = re.search(_ON_DEMAND_HASH_PATTERN.format(chunk_index), page_source)
            if hash_match:
                file_hash = hash_match.group(1)
                url = f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{file_hash}a.js"
                javascript = await session.request(method="GET", url=url, headers=headers)
                key_byte_indices = [
                    int(match.group(1))
                    for match in _KEY_BYTE_INDEX_PATTERN.finditer(javascript.text)
                ]

        if not key_byte_indices:
            raise RuntimeError("Couldn't get KEY_BYTE indices")
        return key_byte_indices[0], key_byte_indices[1:]


def install_transaction_patch(client: Any) -> bool:
    current = getattr(client, "client_transaction", None)
    if isinstance(current, PatchedClientTransaction):
        return False
    if hasattr(upstream_transaction, "ON_DEMAND_HASH_PATTERN"):
        return False
    if not isinstance(current, ClientTransaction):
        return False

    client.client_transaction = PatchedClientTransaction()
    logger.warning("Applied temporary Twikit transaction compatibility patch")
    return True


def install_user_parser_patch() -> bool:
    current_init = TwikitUser.__init__
    if getattr(current_init, "_tweetbot_compat", False):
        return False

    TwikitUser.__init__ = _patched_user_init
    logger.warning("Applied temporary Twikit user parser compatibility patch")
    return True
