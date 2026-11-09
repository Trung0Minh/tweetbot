from datetime import UTC, datetime
from types import SimpleNamespace

from app.delivery import DiscordSender, build_message
from app.models import Subscription


class FakeWebhook:
    def __init__(self, bot_user_id=42):
        self.name = "TweetBot Relay"
        self.user = SimpleNamespace(id=bot_user_id)
        self.token = "webhook-token"
        self.sent = []

    async def send(self, content, **kwargs):
        self.sent.append((content, kwargs))


class FakeChannel:
    def __init__(self, webhooks=None):
        self.id = 3
        self.available_webhooks = list(webhooks or [])
        self.created_webhooks = []

    async def webhooks(self):
        return self.available_webhooks

    async def create_webhook(self, *, name, reason):
        webhook = FakeWebhook()
        webhook.name = name
        self.created_webhooks.append((webhook, reason))
        self.available_webhooks.append(webhook)
        return webhook


class FakeBot:
    def __init__(self, channel):
        self.channel = channel
        self.user = SimpleNamespace(id=42)

    def get_channel(self, channel_id):
        return self.channel


def subscription(ping_role_id=None):
    now = datetime.now(UTC)
    return Subscription(
        id=1,
        guild_id=2,
        channel_id=3,
        x_user_id="4",
        x_username="example",
        include_reposts=False,
        ping_role_id=ping_role_id,
        start_after_post_id="100",
        created_at=now,
        updated_at=now,
    )


async def test_delivery_uses_account_identity_and_tweeted_link(post_factory):
    channel = FakeChannel()
    sender = DiscordSender(FakeBot(channel))

    await sender.send(subscription(), post_factory("101", has_video=True))

    webhook = channel.created_webhooks[0][0]
    content, kwargs = webhook.sent[0]
    assert "embeds" not in kwargs
    assert content == "[Tweeted](https://fxtwitter.com/example/status/101)"
    assert kwargs["username"] == "Example Account"
    assert kwargs["avatar_url"] == "https://pbs.twimg.com/profile_images/example.jpg"


async def test_delivery_places_optional_role_ping_on_its_own_line(post_factory):
    channel = FakeChannel()
    sender = DiscordSender(FakeBot(channel))

    await sender.send(subscription(99), post_factory("101", is_quote=True))

    webhook = channel.created_webhooks[0][0]
    content, kwargs = webhook.sent[0]
    assert content == "<@&99>\n[Tweeted](https://fxtwitter.com/example/status/101)"
    assert [role.id for role in kwargs["allowed_mentions"].roles] == [99]


async def test_delivery_reuses_existing_bot_webhook(post_factory):
    existing = FakeWebhook()
    channel = FakeChannel([existing])
    sender = DiscordSender(FakeBot(channel))

    await sender.send(subscription(), post_factory("101", is_quote=True))
    await sender.send(subscription(), post_factory("102", is_quote=True))

    assert channel.created_webhooks == []
    assert [message[0] for message in existing.sent] == [
        "[Tweeted](https://fxtwitter.com/example/status/101)",
        "[Tweeted](https://fxtwitter.com/example/status/102)",
    ]


async def test_delivery_rejects_non_x_avatar_hosts(post_factory):
    channel = FakeChannel()
    sender = DiscordSender(FakeBot(channel))

    await sender.send(
        subscription(),
        post_factory("101", avatar_url="https://attacker.example/avatar.jpg"),
    )

    webhook = channel.created_webhooks[0][0]
    assert webhook.sent[0][1]["avatar_url"] is None


async def test_embed_message_is_sent_with_embeds(post_factory):
    channel = FakeChannel()
    sender = DiscordSender(FakeBot(channel))

    await sender.send(
        subscription(99),
        post_factory("101", text="hi", image_urls=("https://pbs.twimg.com/a.jpg",)),
    )

    content, kwargs = channel.created_webhooks[0][0].sent[0]
    assert content == "<@&99>"
    assert len(kwargs["embeds"]) == 2
    assert [role.id for role in kwargs["allowed_mentions"].roles] == [99]


def test_text_only_post_is_one_embed(post_factory):
    content, embeds = build_message(post_factory("101", text="hello"), None)

    assert content is None
    assert len(embeds) == 1
    assert embeds[0].description == "hello"
    assert embeds[0].author.name == "@example"
    assert embeds[0].author.url == "https://x.com/example/status/101"
    assert embeds[0].author.icon_url == "https://pbs.twimg.com/profile_images/example.jpg"
    assert embeds[0].timestamp is not None


def test_images_become_separate_embeds_without_url(post_factory):
    urls = tuple(f"https://pbs.twimg.com/media/{i}.jpg?name=large" for i in range(4))
    _, embeds = build_message(post_factory("101", text="hi", image_urls=urls), None)

    assert len(embeds) == 5
    assert embeds[0].image.url is None
    assert [e.image.url for e in embeds[1:]] == list(urls)
    assert all(e.url is None for e in embeds)


def test_ping_is_the_only_content(post_factory):
    content, embeds = build_message(post_factory("101", text="hi"), 99)

    assert content == "<@&99>"
    assert embeds is not None


def test_video_quote_and_repost_fall_back_to_link(post_factory):
    for flag in ("has_video", "is_quote", "is_repost"):
        content, embeds = build_message(post_factory("101", **{flag: True}), None)
        assert embeds is None
        assert content == "[Tweeted](https://fxtwitter.com/example/status/101)"


def test_long_text_is_truncated(post_factory):
    _, embeds = build_message(post_factory("101", text="x" * 5000), None)

    assert len(embeds[0].description) == 4096
    assert embeds[0].description.endswith("\u2026")


def test_non_x_avatar_is_dropped_from_embed(post_factory):
    _, embeds = build_message(
        post_factory("101", avatar_url="https://attacker.example/a.jpg"), None
    )

    assert embeds[0].author.icon_url is None


def test_images_with_empty_text_keep_author_embed(post_factory):
    _, embeds = build_message(
        post_factory("101", image_urls=("https://pbs.twimg.com/a.jpg",)), None
    )

    assert len(embeds) == 2
    assert embeds[0].description is None


def test_hashtags_and_mentions_become_masked_links(post_factory):
    post = post_factory("101", text="hi @Bob #AI", hashtags=("ai",), mentions=("bob",))
    _, embeds = build_message(post, None)

    assert embeds[0].description == ("hi [@Bob](https://x.com/Bob) [#AI](https://x.com/hashtag/AI)")


def test_non_entity_tokens_partial_matches_emails_and_urls_are_left_alone(post_factory):
    text = "#nope @nobody #AIart a@bob.com https://e.com/#AI https://e.com/@bob"
    post = post_factory("101", text=text, hashtags=("AI",), mentions=("bob",))
    _, embeds = build_message(post, None)

    assert embeds[0].description == text


def test_overlong_linked_text_falls_back_to_plain_truncation(post_factory):
    text = "#AI " * 1500
    post = post_factory("101", text=text, hashtags=("AI",))
    _, embeds = build_message(post, None)

    assert len(embeds[0].description) == 4096
    assert "](" not in embeds[0].description
