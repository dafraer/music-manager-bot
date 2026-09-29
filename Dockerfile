# syntax=docker/dockerfile:1
FROM python:3.13-alpine3.23 AS dependencies

WORKDIR /build
COPY requirements.txt .
# Install wheels only; keep pip and build tooling out of the application venv.
RUN python -m venv --without-pip /opt/venv \
    && pip --python /opt/venv install --no-cache-dir --no-compile \
        --only-binary=:all: -r requirements.txt

FROM python:3.13-alpine3.23 AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Alpine's packages provide FFmpeg, ffprobe, and yt-dlp's JS runtime.
RUN apk add --no-cache ffmpeg nodejs ca-certificates \
    && addgroup -S bot && adduser -S -G bot bot

WORKDIR /app
COPY --from=dependencies /opt/venv /opt/venv
COPY bot/ ./bot/

USER bot
CMD ["python", "-m", "bot"]
