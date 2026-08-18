from datetime import UTC, datetime
from types import SimpleNamespace

from app.delivery import DiscordSender
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

    await sender.send(subscription(), post_factory("101"))

    webhook = channel.created_webhooks[0][0]
    content, kwargs = webhook.sent[0]
    assert content == "[Tweeted](https://twitter.com/example/status/101)"
    assert kwargs["username"] == "Example Account"
    assert kwargs["avatar_url"] == "https://pbs.twimg.com/profile_images/example.jpg"


async def test_delivery_places_optional_role_ping_on_its_own_line(post_factory):
    channel = FakeChannel()
    sender = DiscordSender(FakeBot(channel))

    await sender.send(subscription(99), post_factory("101"))

    webhook = channel.created_webhooks[0][0]
    content, kwargs = webhook.sent[0]
    assert content == "<@&99>\n[Tweeted](https://twitter.com/example/status/101)"
    assert [role.id for role in kwargs["allowed_mentions"].roles] == [99]


async def test_delivery_reuses_existing_bot_webhook(post_factory):
    existing = FakeWebhook()
    channel = FakeChannel([existing])
    sender = DiscordSender(FakeBot(channel))

    await sender.send(subscription(), post_factory("101"))
    await sender.send(subscription(), post_factory("102"))

    assert channel.created_webhooks == []
    assert [message[0] for message in existing.sent] == [
        "[Tweeted](https://twitter.com/example/status/101)",
        "[Tweeted](https://twitter.com/example/status/102)",
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
