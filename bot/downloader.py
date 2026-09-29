import asyncio
import json
import logging
import os
import re
import signal
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .config import Settings

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    """A download failure with a message suitable for the requesting user."""


@dataclass(frozen=True, slots=True)
class Audio:
    path: Path
    title: str
    performer: str | None
    duration: int


def normalize_youtube_url(text: str) -> str:
    """Accept one video URL and discard playlists, tracking, and other parameters."""
    try:
        url = urlsplit(text.strip())
        if (
            url.scheme not in {"http", "https"}
            or url.username is not None
            or url.password is not None
            or url.port not in {None, 80, 443}
        ):
            raise ValueError
        if url.hostname in {"youtu.be", "www.youtu.be"}:
            video_id = url.path.removeprefix("/")
        elif url.hostname in {
            "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
        }:
            if url.path == "/watch":
                video_id = parse_qs(url.query).get("v", [""])[0]
            else:
                match = re.fullmatch(r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})/?", url.path)
                video_id = match[1] if match else ""
        else:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError
    except ValueError:
        raise ValueError("Send a single YouTube video URL (watch, Shorts, or youtu.be).") from None
    return f"https://www.youtube.com/watch?v={video_id}"


async def download_audio(url: str, directory: Path, settings: Settings) -> Audio:
    """Run yt-dlp outside the event loop; the caller owns the temporary directory."""
    command = [
        sys.executable, "-m", "yt_dlp",
        "--ignore-config", "--no-plugin-dirs", "--no-playlist",
        "--no-progress", "--no-colors", "--no-cache-dir",
        "--js-runtimes", "node",
        "--format", "bestaudio/best",
        "--extract-audio", "--audio-format", "mp3",
        "--audio-quality", f"{settings.audio_bitrate}K",
        "--embed-metadata",
        "--match-filters", f"!is_live & duration <= {settings.max_duration_seconds}",
        "--max-filesize", str(settings.max_download_mb * 1_000_000),
        "--socket-timeout", "20", "--retries", "2", "--fragment-retries", "2",
        "--abort-on-unavailable-fragments",
        "--output", str(directory / "audio.%(ext)s"),
        "--print", "after_move:%(.{title,uploader,duration})j",
        "--", normalize_youtube_url(url),
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=settings.download_timeout_seconds,
        )
    except TimeoutError:
        raise DownloadError("The download took too long. Try a shorter video or try again later.") from None
    finally:
        if process.returncode is None:
            # Kill the entire process group, including FFmpeg/JS children, before cleanup.
            # This also runs when a handler is cancelled during shutdown.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.communicate()

    if process.returncode != 0:
        logger.warning("yt-dlp failed: %s", stderr.decode(errors="replace")[-2000:])
        raise DownloadError(
            "Could not download this video. It may be private, unavailable, or blocked by YouTube."
        )

    path = directory / "audio.mp3"
    if not path.is_file():
        raise DownloadError(
            "Video skipped: it may be live, have no known duration, or exceed the "
            f"{settings.max_duration_seconds}-second / {settings.max_download_mb} MB download limit."
        )
    if not 0 < path.stat().st_size <= settings.max_upload_mb * 1_000_000:
        raise DownloadError(
            f"The MP3 is empty or exceeds the {settings.max_upload_mb} MB upload limit. "
            "Try a shorter video or lower AUDIO_BITRATE."
        )

    metadata = json.loads(stdout)
    return Audio(
        path=path,
        title=str(metadata.get("title") or "YouTube audio")[:128],
        performer=str(metadata["uploader"])[:128] if metadata.get("uploader") else None,
        duration=int(metadata.get("duration") or 0),
    )
