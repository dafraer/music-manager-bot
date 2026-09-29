import asyncio
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import ErrorEvent, FSInputFile, Message

from .config import Settings
from .downloader import DownloadError, download_audio, normalize_youtube_url

logger = logging.getLogger(__name__)


def create_router(settings: Settings, active: dict[int, asyncio.Task]) -> Router:
    router = Router()
    router.message.filter(F.chat.type == ChatType.PRIVATE)

    @router.message(Command("start", "help"))
    async def help_message(message: Message) -> None:
        if message.from_user.id not in settings.allowed_user_ids:
            await message.answer("You are not allowed to use this bot.")
            return
        await message.answer(
            "Welcome! Send me a YouTube video link and I'll convert it to MP3 "
            "and post it to your channel."
        )

    @router.message()
    async def submit_video(message: Message, bot: Bot) -> None:
        user_id = message.from_user.id
        if user_id not in settings.allowed_user_ids:
            await message.answer("You are not allowed to use this bot.")
            return
        try:
            url = normalize_youtube_url(message.text or "")
        except ValueError as exc:
            await message.answer(str(exc))
            return
        if user_id in active:
            await message.answer("Your previous video is still processing. Please wait.")
            return
        if len(active) >= settings.max_concurrent_downloads:
            await message.answer("The bot is busy. Please try again once a download finishes.")
            return

        # Reserve before the first await: there is no unbounded queue of downloads.
        active[user_id] = asyncio.current_task()
        try:
            status = await message.answer("Downloading and converting to MP3…")
            try:
                # Keep the file until Telegram finishes reading it, then always delete it.
                with TemporaryDirectory(prefix="music-bot-") as directory:
                    audio = await download_audio(url, Path(directory), settings)
                    await status.edit_text("Uploading MP3 to the channel…")
                    await bot.send_audio(
                        chat_id=settings.channel_id,
                        audio=FSInputFile(audio.path),
                        title=audio.title,
                        performer=audio.performer,
                        duration=audio.duration,
                        request_timeout=settings.upload_timeout_seconds,
                    )
            except DownloadError as exc:
                await status.edit_text(str(exc))
                return
            except TelegramRetryAfter as exc:
                await message.answer(f"Telegram is rate-limiting the bot. Try again in {exc.retry_after}s.")
                return
            except TelegramAPIError:
                logger.exception("Telegram upload/status request failed")
                await message.answer(
                    "Telegram could not confirm the upload. Check the channel before retrying, "
                    "and verify the bot still has permission to post."
                )
                return

            # A failed confirmation must not be reported as a failed channel upload.
            try:
                await status.edit_text("MP3 sent to the channel.")
            except TelegramAPIError:
                logger.warning("MP3 was sent, but the confirmation could not be updated")
        finally:
            active.pop(user_id, None)

    @router.errors()
    async def handle_error(event: ErrorEvent) -> bool:
        logger.error(
            "Unhandled update error",
            exc_info=(type(event.exception), event.exception, event.exception.__traceback__),
        )
        if event.update.message:
            try:
                await event.update.message.answer("An unexpected error occurred. Please try again later.")
            except TelegramAPIError:
                logger.warning("Could not deliver the error message")
        return True

    return router
