import asyncio
import logging
import shutil

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatMemberStatus, ChatType

from .config import Settings
from .handlers import create_router


async def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for executable in ("ffmpeg", "ffprobe", "node"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"Required executable not found: {executable}")

    active: dict[int, asyncio.Task] = {}
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.include_router(create_router(settings, active))
    async with Bot(token=settings.bot_token) as bot:
        channel = await bot.get_chat(settings.channel_id)
        membership = await bot.get_chat_member(channel.id, bot.id)
        if channel.type != ChatType.CHANNEL:
            raise RuntimeError("CHANNEL_ID must point to a Telegram channel.")
        if membership.status != ChatMemberStatus.ADMINISTRATOR or not membership.can_post_messages:
            raise RuntimeError("Make the bot a channel administrator with Post Messages permission.")

        # Polling and webhooks cannot be used simultaneously. Preserve pending updates.
        await bot.delete_webhook(drop_pending_updates=False)
        logging.getLogger(__name__).info("Starting bot; destination channel: %s", channel.id)
        try:
            await dispatcher.start_polling(
                bot,
                allowed_updates=["message"],
                close_bot_session=False,
            )
        finally:
            tasks = list(active.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None
