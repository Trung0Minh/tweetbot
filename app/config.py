from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    poll_interval_seconds: int
    database_path: Path
    x_username: str
    x_email: str
    x_password: str
    x_cookies_path: Path
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> Settings:
        required = ("DISCORD_TOKEN", "X_USERNAME", "X_EMAIL", "X_PASSWORD")
        missing = [name for name in required if not values.get(name, "").strip()]
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")

        try:
            poll_interval = int(values.get("POLL_INTERVAL_SECONDS", "60"))
        except ValueError as exc:
            raise ValueError("POLL_INTERVAL_SECONDS must be an integer") from exc
        if poll_interval <= 0:
            raise ValueError("POLL_INTERVAL_SECONDS must be greater than zero")

        log_level = values.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be a standard Python logging level")

        return cls(
            discord_token=values["DISCORD_TOKEN"].strip(),
            poll_interval_seconds=poll_interval,
            database_path=Path(values.get("DATABASE_PATH", "data/bot.db")),
            x_username=values["X_USERNAME"].strip(),
            x_email=values["X_EMAIL"].strip(),
            x_password=values["X_PASSWORD"],
            x_cookies_path=Path(values.get("X_COOKIES_PATH", "data/x_cookies.json")),
            log_level=log_level,
        )
