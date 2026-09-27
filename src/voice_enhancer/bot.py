import asyncio
import logging
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from voice_enhancer.application.media_validation import (
    AUDIO_EXTENSIONS,
    VIDEO_EXTENSIONS,
    MediaKind,
    MediaMetadata,
    MediaValidationError,
    validate_media,
)
from voice_enhancer.application.processing import MediaProcessingService
from voice_enhancer.config import settings
from voice_enhancer.domain.profile import Preset, profile_for
from voice_enhancer.infrastructure.ffmpeg import FFmpegProcessor

router = Router()
processor = MediaProcessingService(FFmpegProcessor(settings.ffmpeg_bin, settings.ffprobe_bin))
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PendingMedia:
    source: Path
    kind: MediaKind
    temp_dir: tempfile.TemporaryDirectory


pending_media: dict[str, PendingMedia] = {}


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "🎙 Отправь мне видео, аудио или голосовое.\n"
        "Я очищу голос, уберу лишний шум и сделаю звучание плотнее и ближе к профессиональному микрофону."
    )


def _media_from_message(message: Message) -> tuple[str, str, MediaKind] | None:
    if message.video:
        return message.video.file_id, ".mp4", MediaKind.VIDEO
    if message.audio:
        suffix = Path(message.audio.file_name or "audio.m4a").suffix.lower() or ".m4a"
        return message.audio.file_id, suffix, MediaKind.AUDIO
    if message.voice:
        return message.voice.file_id, ".ogg", MediaKind.AUDIO
    if message.document:
        suffix = Path(message.document.file_name or "").suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            return message.document.file_id, suffix, MediaKind.VIDEO
        if suffix in AUDIO_EXTENSIONS:
            return message.document.file_id, suffix, MediaKind.AUDIO
    return None


def _preset_keyboard(token: str) -> InlineKeyboardMarkup:
    labels = {
        Preset.NATURAL: "🌿 Natural",
        Preset.STUDIO: "🎙 Studio",
        Preset.REELS: "📱 Reels",
        Preset.PODCAST: "🎧 Podcast",
    }
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=labels[preset], callback_data=f"p:{token}:{preset.value}")]
            for preset in Preset
        ]
    )


async def _expire_pending(token: str, after_seconds: int = 1800) -> None:
    await asyncio.sleep(after_seconds)
    pending = pending_media.pop(token, None)
    if pending:
        pending.temp_dir.cleanup()


@router.message(F.video | F.audio | F.voice | F.document)
async def receive_media(message: Message, bot: Bot) -> None:
    media = _media_from_message(message)
    if not media:
        await message.answer("Этот формат файла пока не поддерживается.")
        return
    file_id, suffix, kind = media
    telegram_file = await bot.get_file(file_id)
    size = telegram_file.file_size or 0
    if size <= 0 or size > settings.max_media_size_bytes:
        await message.answer("Размер файла превышает допустимый лимит.")
        return

    temp_dir = tempfile.TemporaryDirectory(prefix="voice-enhancer-")
    source = Path(temp_dir.name) / f"source{suffix}"
    try:
        await bot.download(telegram_file, destination=source)
        metadata_json = await processor.ffmpeg.probe(source)
        streams = metadata_json.get("streams", [])
        has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
        has_video = any(stream.get("codec_type") == "video" for stream in streams)
        duration = float(metadata_json.get("format", {}).get("duration", 0) or 0)
        metadata = MediaMetadata(kind, duration, size, has_audio, has_video)
        validate_media(
            source,
            metadata,
            max_duration_seconds=settings.max_media_duration_seconds,
            max_size_bytes=settings.max_media_size_bytes,
        )
    except MediaValidationError as error:
        temp_dir.cleanup()
        await message.answer(str(error))
        return
    except Exception:
        logger.exception("Media validation failed")
        temp_dir.cleanup()
        await message.answer("Не удалось прочитать файл. Проверь формат и попробуй ещё раз.")
        return

    token = uuid.uuid4().hex[:12]
    pending_media[token] = PendingMedia(source, kind, temp_dir)
    asyncio.create_task(_expire_pending(token))
    await message.answer(
        "Файл получен. Как обработать голос?", reply_markup=_preset_keyboard(token)
    )


@router.callback_query(lambda callback: bool(callback.data and callback.data.startswith("p:")))
async def process_preset(callback: CallbackQuery, bot: Bot) -> None:
    assert callback.data is not None
    _, token, preset_value = callback.data.split(":", 2)
    pending = pending_media.pop(token, None)
    if not pending:
        await callback.answer("Файл уже обработан или ссылка устарела.", show_alert=True)
        return
    await callback.answer()
    status = callback.message
    if status is None:
        pending.temp_dir.cleanup()
        return
    await status.edit_text("Обрабатываю голос…")
    try:
        profile = profile_for(Preset(preset_value))
        result = await processor.process(pending.source, kind=pending.kind, profile=profile)
        upload = FSInputFile(result.output_path, filename=result.output_path.name)
        if pending.kind is MediaKind.VIDEO:
            await bot.send_video(
                status.chat.id, upload, caption="Готово! Видео с обработанным голосом."
            )
        else:
            await bot.send_audio(
                status.chat.id, upload, caption="Готово! Аудио с обработанным голосом."
            )
        await status.delete()
    except Exception:
        logger.exception("Media processing failed")
        await status.edit_text("Не удалось обработать файл. Попробуй ещё раз чуть позже.")
    finally:
        pending.temp_dir.cleanup()


async def _run() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required to start Telegram polling")
    logging.basicConfig(level=settings.log_level)
    bot = Bot(settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)


def run() -> None:
    asyncio.run(_run())
