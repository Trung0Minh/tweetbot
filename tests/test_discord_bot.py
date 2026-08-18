from app.discord_bot import DiscordFeedBot


class FakeXService:
    async def resolve_user(self, username):
        raise NotImplementedError

    async def get_recent_posts(self, user_id):
        return []


async def test_bot_registers_only_the_four_v1_slash_commands(tmp_path):
    from app.db import Database

    bot = DiscordFeedBot(Database(tmp_path / "bot.db"), FakeXService(), 60)

    await bot.install_commands(sync=False)

    assert {command.name for command in bot.tree.get_commands()} == {
        "follow",
        "unfollow",
        "follows",
        "status",
    }
    await bot.close()
