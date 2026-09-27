from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from voice_enhancer.config import Settings


def make_bot(settings: Settings) -> Bot:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required")
    if settings.telegram_api_base_url:
        session = AiohttpSession(
            api=TelegramAPIServer.from_base(settings.telegram_api_base_url, is_local=True)
        )
        return Bot(settings.bot_token, session=session)
    return Bot(settings.bot_token)
