from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    poll_interval_seconds: int
    database_path: Path
    x_username: str
    x_email: str
    x_password: str
    x_auth_token: str
    x_ct0: str
    x_cookies_path: Path
    log_level: str
    turso_database_url: str = ""
    turso_auth_token: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> Settings:
        required = ("DISCORD_TOKEN", "X_USERNAME")
        missing = [name for name in required if not values.get(name, "").strip()]
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")

        turso_url = values.get("TURSO_DATABASE_URL", "").strip()
        turso_token = values.get("TURSO_AUTH_TOKEN", "").strip()
        if bool(turso_url) != bool(turso_token):
            raise ValueError("TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set together")
        if turso_url:
            try:
                parsed = urlsplit(turso_url)
                valid = (
                    parsed.scheme in {"libsql", "https"}
                    and parsed.hostname
                    and not parsed.username
                    and not parsed.password
                    and not parsed.query
                    and not parsed.fragment
                    and parsed.path in {"", "/"}
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("TURSO_DATABASE_URL must be a secure libsql:// or https:// URL")

        x_auth_token = values.get("X_AUTH_TOKEN", "").strip()
        x_ct0 = values.get("X_CT0", "").strip()
        if bool(x_auth_token) != bool(x_ct0):
            raise ValueError("X_AUTH_TOKEN and X_CT0 must be set together")
        if not x_auth_token:
            missing = [
                name for name in ("X_EMAIL", "X_PASSWORD") if not values.get(name, "").strip()
            ]
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
            x_email=values.get("X_EMAIL", "").strip(),
            x_password=values.get("X_PASSWORD", ""),
            x_auth_token=x_auth_token,
            x_ct0=x_ct0,
            x_cookies_path=Path(values.get("X_COOKIES_PATH", "data/x_cookies.json")),
            log_level=log_level,
            turso_database_url=turso_url,
            turso_auth_token=turso_token,
        )
