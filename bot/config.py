import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from aiogram.utils.token import TokenValidationError, validate_token
from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str = field(repr=False)
    channel_id: int | str
    allowed_user_ids: frozenset[int]
    audio_bitrate: int
    max_duration_seconds: int
    max_concurrent_downloads: int
    download_timeout_seconds: int
    upload_timeout_seconds: int
    max_upload_mb: int
    max_download_mb: int
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        # Use the project's .env, never an unrelated file in a parent directory.
        # Docker injects the same file as environment variables instead.
        load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
        token = os.getenv("BOT_TOKEN", "").strip()
        try:
            validate_token(token)
        except TokenValidationError:
            raise ValueError("Set BOT_TOKEN to a valid BotFather token in .env.") from None

        channel = os.getenv("CHANNEL_ID", "").strip()
        if not re.fullmatch(r"-100[0-9]+|@[A-Za-z][A-Za-z0-9_]{4,31}", channel):
            raise ValueError("CHANNEL_ID must be @channel_username or a -100... channel ID.")

        try:
            user_ids = frozenset(
                int(value.strip())
                for value in os.getenv("ALLOWED_USER_IDS", "").split(",")
            )
        except ValueError:
            raise ValueError("ALLOWED_USER_IDS must contain comma-separated user IDs.") from None
        if not user_ids or any(user_id <= 0 for user_id in user_ids):
            raise ValueError("ALLOWED_USER_IDS must contain positive Telegram user IDs.")

        defaults = {
            "AUDIO_BITRATE": 192,
            "MAX_DURATION_SECONDS": 1200,
            "MAX_CONCURRENT_DOWNLOADS": 1,
            "DOWNLOAD_TIMEOUT_SECONDS": 300,
            "UPLOAD_TIMEOUT_SECONDS": 120,
            "MAX_UPLOAD_MB": 49,
            "MAX_DOWNLOAD_MB": 100,
        }
        values = {}
        for name, default in defaults.items():
            try:
                value = int(os.getenv(name, str(default)))
            except ValueError:
                raise ValueError(f"{name} must be a positive integer.") from None
            if value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
            values[name.lower()] = value

        if values["audio_bitrate"] not in {64, 96, 128, 160, 192, 256, 320}:
            raise ValueError("AUDIO_BITRATE must be 64, 96, 128, 160, 192, 256, or 320.")
        if values["max_upload_mb"] > 49:
            raise ValueError("MAX_UPLOAD_MB must be at most 49 for Telegram uploads.")
        level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL.")

        return cls(
            bot_token=token,
            channel_id=int(channel) if channel.startswith("-") else channel,
            allowed_user_ids=user_ids,
            log_level=level,
            **values,
        )
