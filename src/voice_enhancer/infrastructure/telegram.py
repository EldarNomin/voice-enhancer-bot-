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


async def configure_profile(bot: Bot) -> None:
    """Set the default English profile and its localized Russian variant."""
    await bot.set_my_name(name="Voice Enhancer Studio")
    await bot.set_my_name(name="Улучшить голос | Чистый звук", language_code="ru")
    await bot.set_my_description(
        description="Send a video, audio file, or voice message to enhance speech and reduce noise."
    )
    await bot.set_my_description(
        description="Пришли видео, аудио или голосовое сообщение — улучшу звучание речи и уменьшу шум.",
        language_code="ru",
    )
    await bot.set_my_short_description(
        short_description="Enhance speech in videos and audio right in Telegram."
    )
    await bot.set_my_short_description(
        short_description="Улучшение голоса в видео и аудио прямо в Telegram.", language_code="ru"
    )
