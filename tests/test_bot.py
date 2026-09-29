import asyncio
import json
import os
import shutil
import signal
import subprocess
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendAudio
from aiogram.types import Message, Update

from bot.config import Settings
from bot.downloader import Audio, DownloadError, download_audio, normalize_youtube_url
from bot.handlers import create_router

TOKEN = "123456789:test-token"
URL = "https://www.youtube.com/watch?v=BaW_jenozKc"
SETTINGS = Settings(
    bot_token=TOKEN, channel_id=-1001234567890, allowed_user_ids=frozenset({42}),
    audio_bitrate=192, max_duration_seconds=1200, max_concurrent_downloads=1,
    download_timeout_seconds=300, upload_timeout_seconds=120,
    max_upload_mb=49, max_download_mb=100, log_level="INFO",
)


class ConfigAndURLTests(unittest.TestCase):
    def test_normalizes_supported_links_without_playlist_or_tracking(self):
        for url in (
            URL + "&list=playlist&index=4",
            "https://youtu.be/BaW_jenozKc?si=tracking",
            "https://www.youtube.com/shorts/BaW_jenozKc",
            "https://m.youtube.com/live/BaW_jenozKc",
            "https://music.youtube.com/watch?v=BaW_jenozKc",
        ):
            with self.subTest(url=url):
                self.assertEqual(normalize_youtube_url(url), URL)

    def test_rejects_other_hosts_and_non_video_urls(self):
        for url in (
            "", "file:///etc/passwd", "https://youtube.com.evil.test/watch?v=BaW_jenozKc",
            "https://youtube.com@evil.test/watch?v=BaW_jenozKc",
            "https://youtube.com/playlist?list=abc", "https://youtu.be/not-an-id",
            "https://youtube.com:bad/watch?v=BaW_jenozKc", URL + " another link",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_youtube_url(url)

    def test_config_parses_ids_and_keeps_token_out_of_repr(self):
        with patch.dict(os.environ, {
            "BOT_TOKEN": TOKEN, "CHANNEL_ID": "-1001234567890", "ALLOWED_USER_IDS": "42, 43",
        }, clear=True), patch("bot.config.load_dotenv"):
            settings = Settings.from_env()
        self.assertEqual(settings.channel_id, -1001234567890)
        self.assertEqual(settings.allowed_user_ids, frozenset({42, 43}))
        self.assertNotIn(TOKEN, repr(settings))

    def test_invalid_configuration_fails_early(self):
        for key, value in (
            ("BOT_TOKEN", ""), ("CHANNEL_ID", "123"), ("ALLOWED_USER_IDS", ""),
            ("ALLOWED_USER_IDS", "-42"), ("MAX_UPLOAD_MB", "50"),
            ("AUDIO_BITRATE", "123"), ("DOWNLOAD_TIMEOUT_SECONDS", "0"),
            ("MAX_CONCURRENT_DOWNLOADS", "abc"), ("LOG_LEVEL", "UNKNOWN"),
        ):
            env = {"BOT_TOKEN": TOKEN, "CHANNEL_ID": "@music_channel", "ALLOWED_USER_IDS": "42"}
            env[key] = value
            with self.subTest(key=key, value=value), patch.dict(os.environ, env, clear=True):
                with patch("bot.config.load_dotenv"), self.assertRaises(ValueError):
                    Settings.from_env()


class DownloaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_and_cancellation_kill_the_process_group(self):
        for exception in (TimeoutError(), asyncio.CancelledError()):
            process = SimpleNamespace(
                pid=12345, returncode=None,
                communicate=AsyncMock(side_effect=[exception, (b"", b"")]),
            )
            expected = DownloadError if isinstance(exception, TimeoutError) else asyncio.CancelledError
            with self.subTest(exception=type(exception).__name__):
                with patch("bot.downloader.asyncio.create_subprocess_exec", return_value=process):
                    with patch("bot.downloader.os.killpg") as kill:
                        with self.assertRaises(expected):
                            await download_audio(URL, Path("."), SETTINGS)
                kill.assert_called_once_with(12345, signal.SIGKILL)
                self.assertEqual(process.communicate.await_count, 2)

    async def test_failed_or_skipped_download_does_not_return_audio(self):
        with TemporaryDirectory(dir=".") as directory:
            for code in (0, 1):
                process = SimpleNamespace(
                    returncode=code, communicate=AsyncMock(return_value=(b"", b"unavailable")),
                )
                with self.subTest(code=code):
                    with patch("bot.downloader.asyncio.create_subprocess_exec", return_value=process):
                        with self.assertRaises(DownloadError):
                            await download_audio(URL, Path(directory), SETTINGS)

    async def test_oversized_mp3_is_rejected_before_upload(self):
        with TemporaryDirectory(dir=".") as directory:
            path = Path(directory) / "audio.mp3"
            with path.open("wb") as file:
                file.truncate(1_000_001)
            process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"{}", b"")))
            with patch("bot.downloader.asyncio.create_subprocess_exec", return_value=process):
                with self.assertRaisesRegex(DownloadError, "upload limit"):
                    await download_audio(URL, Path(directory), replace(SETTINGS, max_upload_mb=1))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    async def test_real_ytdlp_and_ffmpeg_pipeline_with_local_audio(self):
        # Exercise the real CLI and conversion without depending on YouTube availability.
        with TemporaryDirectory(dir=".") as directory:
            directory = Path(directory).resolve()
            source = directory / "source.wav"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "sine=frequency=440:duration=0.1", str(source),
            ], check=True)
            info = directory / "source.json"
            info.write_text(json.dumps({
                "id": "local-test", "title": "Test tone", "uploader": "Test artist",
                "duration": 0.1, "url": source.as_uri(), "ext": "wav", "extractor": "generic",
            }))
            create_process = asyncio.create_subprocess_exec

            async def use_local_source(*command, **kwargs):
                return await create_process(
                    *command[:-2], "--enable-file-urls", "--load-info-json", str(info), **kwargs,
                )

            with patch("bot.downloader.asyncio.create_subprocess_exec", side_effect=use_local_source):
                audio = await download_audio(URL, directory, SETTINGS)
            self.assertEqual(audio.title, "Test tone")
            self.assertEqual(audio.performer, "Test artist")
            self.assertGreater(audio.path.stat().st_size, 0)
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1", str(audio.path),
            ], check=True, capture_output=True, text=True)
            self.assertEqual(probe.stdout.strip(), "mp3")


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.active = {}
        self.bot = Bot(TOKEN)
        self.dispatcher = Dispatcher(disable_fsm=True)
        self.dispatcher.include_router(create_router(SETTINGS, self.active))
        self.paths = []
        self.message = Message.model_validate({
            "message_id": 1, "date": 0, "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "Test"}, "text": URL,
        })

        async def session(bot, method, **kwargs):
            if isinstance(method, SendAudio):
                self.assertTrue(Path(method.audio.path).exists())
                self.assertEqual(method.chat_id, SETTINGS.channel_id)
                self.assertIsNone(method.caption)
            return self.message.as_(bot)

        self.session = AsyncMock(side_effect=session)
        self.bot.session = self.session

        async def download(url, directory, settings):
            path = directory / "audio.mp3"
            path.write_bytes(b"test audio")
            self.paths.append(path)
            return Audio(path, "Title", "Artist", 10)

        self.download = self.enterContext(patch("bot.handlers.download_audio", side_effect=download))
        self.enterContext(patch(
            "bot.handlers.TemporaryDirectory",
            side_effect=lambda **kwargs: TemporaryDirectory(dir=".", **kwargs),
        ))

    async def feed(self, **changes):
        message = self.message.model_copy(update=changes)
        await self.dispatcher.feed_update(self.bot, Update(update_id=1, message=message))

    async def test_success_uploads_then_cleans_up(self):
        await self.feed()
        self.download.assert_awaited_once()
        self.assertTrue(any(isinstance(call.args[1], SendAudio) for call in self.session.await_args_list))
        self.assertFalse(self.paths[0].exists())
        self.assertEqual(self.active, {})

    async def test_unauthorized_user_cannot_download(self):
        for text in (URL, "/start", "/help"):
            with self.subTest(text=text):
                self.session.reset_mock()
                await self.feed(
                    text=text,
                    from_user=self.message.from_user.model_copy(update={"id": 99}),
                )
                self.download.assert_not_awaited()
                self.session.assert_awaited_once()
                self.assertEqual(
                    self.session.await_args.args[1].text,
                    "You are not allowed to use this bot.",
                )

    async def test_busy_and_duplicate_requests_cannot_download(self):
        for user_id in (42, 43):
            with self.subTest(active_user=user_id):
                self.active.clear()
                self.active[user_id] = asyncio.current_task()
                await self.feed()
                self.download.assert_not_awaited()

    async def test_group_messages_are_ignored(self):
        await self.feed(chat=self.message.chat.model_copy(update={"type": "group"}))
        self.download.assert_not_awaited()
        self.session.assert_not_awaited()

    async def test_telegram_upload_failure_still_cleans_up(self):
        original_session = self.session.side_effect

        async def reject_upload(bot, method, **kwargs):
            if isinstance(method, SendAudio):
                raise TelegramBadRequest(method=method, message="Cannot post")
            return await original_session(bot, method, **kwargs)

        self.session.side_effect = reject_upload
        with self.assertLogs("bot.handlers", level="ERROR"):
            await self.feed()
        self.assertFalse(self.paths[0].exists())
        self.assertEqual(self.active, {})

    async def test_cancellation_releases_slot_and_temporary_directory(self):
        started = asyncio.Event()

        async def wait_for_cancel(url, directory, settings):
            self.paths.append(directory)
            started.set()
            await asyncio.Event().wait()

        self.download.side_effect = wait_for_cancel
        task = asyncio.create_task(self.feed())
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.paths[0].exists())
        self.assertEqual(self.active, {})


if __name__ == "__main__":
    unittest.main()
