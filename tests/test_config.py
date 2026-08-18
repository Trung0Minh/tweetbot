import pytest

from app.config import Settings


def test_settings_require_discord_token_and_x_credentials(tmp_path):
    with pytest.raises(ValueError, match="DISCORD_TOKEN"):
        Settings.from_mapping({})

    settings = Settings.from_mapping(
        {
            "DISCORD_TOKEN": "discord-token",
            "X_USERNAME": "bot-account",
            "X_EMAIL": "bot@example.com",
            "X_PASSWORD": "secret",
            "DATABASE_PATH": str(tmp_path / "bot.db"),
            "X_COOKIES_PATH": str(tmp_path / "cookies.json"),
        }
    )

    assert settings.poll_interval_seconds == 60
    assert settings.database_path == tmp_path / "bot.db"


def test_settings_reject_invalid_poll_interval(tmp_path):
    values = {
        "DISCORD_TOKEN": "discord-token",
        "X_USERNAME": "bot-account",
        "X_EMAIL": "bot@example.com",
        "X_PASSWORD": "secret",
        "POLL_INTERVAL_SECONDS": "0",
        "DATABASE_PATH": str(tmp_path / "bot.db"),
        "X_COOKIES_PATH": str(tmp_path / "cookies.json"),
    }

    with pytest.raises(ValueError, match="POLL_INTERVAL_SECONDS"):
        Settings.from_mapping(values)
