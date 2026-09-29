# YouTube → MP3 → Telegram channel

A small aiogram bot that accepts a YouTube video URL in a private message,
downloads audio with yt-dlp, converts it to MP3 with FFmpeg, and uploads it as
playable audio to one configured Telegram channel.

## Configure Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Add the bot to your channel as an **administrator** with **Post Messages** permission.
3. Edit `.env` (or copy `.env.example` to `.env` in a fresh checkout):

   ```dotenv
   BOT_TOKEN=123456789:your_bot_token
   CHANNEL_ID=@your_channel
   ALLOWED_USER_IDS=123456789
   ```

   `CHANNEL_ID` can also be a private channel's numeric `-100...` ID. For a
   private channel, copy a post link: `https://t.me/c/1234567890/42` corresponds
   to channel ID `-1001234567890`. Use your **user ID**, not your username, in
   `ALLOWED_USER_IDS`; separate multiple IDs with commas. You can obtain your
   ID from @userinfobot.

The bot checks channel access and posting permissions on startup. Credentials
are excluded from Git and Docker's build context. All application settings are
listed in `.env.example`; `.env` is the configuration source for both local and
Compose runs. Existing environment variables take precedence locally.

## Run with Docker Compose

Requires Docker with the Compose plugin and a running Docker daemon.

```sh
docker compose up --build -d
docker compose logs -f bot
```

After changing `.env`, run `docker compose up -d` to recreate the container.
Stop it with `docker compose down`. Run only one instance per bot token.
No published ports or persistent storage are needed: the bot uses long polling.

The multi-stage Alpine image uses binary Python wheels, no pip cache, and a
non-root runtime. FFmpeg and Node.js are included. Compose uses a read-only
root filesystem and a 512 MB temporary filesystem for downloads. If increasing
download size/concurrency significantly, increase that temporary storage too.

## Run locally (macOS / Linux)

Requires Python 3.13+, FFmpeg (including ffprobe), and Node.js 22+ on `PATH`.
Node.js and the bundled `yt-dlp-ejs` dependency support YouTube's JavaScript
challenges. For macOS, install system dependencies with:

```sh
brew install python@3.13 ffmpeg node
```

Then, from the project directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m bot
```

## Use

Open a private chat with the bot and send one URL, for example:

```text
https://www.youtube.com/watch?v=BaW_jenozKc
```

Standard watch links, `youtu.be`, Shorts, music.youtube.com, and recorded
`/live/` links are supported. A video link containing a playlist parameter
downloads only that video; playlist-only links are rejected. Send the URL on
its own, without additional text.

Defaults:

- MP3 with title/artist information, posted without a YouTube link caption.
- Only users listed in `ALLOWED_USER_IDS` can submit downloads.
- One active request at a time; excess requests receive a busy response.
- Maximum video duration: 20 minutes. Live streams and unknown-duration videos are skipped.
- Maximum source download: 100 MB when yt-dlp can determine/enforce the size.
- Maximum final upload: 49 MB, below Telegram's standard 50 MB audio limit.
- Download/conversion timeout: 300 seconds. Upload timeout: 120 seconds.
- Temporary files are deleted after success, failure, or graceful shutdown.

Jobs are not persisted across restarts. On shutdown an active job is cancelled;
send its link again after restart if it was not posted. If a Telegram upload
times out, check the channel before retrying: Telegram may have received it.

YouTube can reject downloads from some IPs or require sign-in. Such failures
are reported to the requester; details appear in logs. Because YouTube changes
frequently, update the yt-dlp pin in `requirements.txt` when a newer release is
needed, then reinstall dependencies or rebuild the container.

## Structure and checks

- `bot/config.py`: `.env` loading and validation.
- `bot/downloader.py`: URL validation, yt-dlp subprocess, MP3 checks.
- `bot/handlers.py`: Telegram requests, access control, bounded concurrency, upload.
- `bot/__main__.py`: dependency checks, channel validation, polling, shutdown.

```sh
python -m unittest discover -s tests -v
python -m pip check
docker compose config --quiet
```

Tests use mocked network boundaries; real YouTube and Telegram delivery require
a configured bot and a reachable video. The subprocess lifecycle uses POSIX
process groups; use Docker on Windows.
