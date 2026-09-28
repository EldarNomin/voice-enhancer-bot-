import asyncio
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from redis.asyncio import Redis

from voice_enhancer.application.media_validation import (
    AUDIO_EXTENSIONS,
    VIDEO_EXTENSIONS,
    MediaKind,
    MediaMetadata,
    MediaValidationError,
    validate_media,
)
from voice_enhancer.config import settings
from voice_enhancer.domain.job import JobStatus
from voice_enhancer.domain.profile import Preset
from voice_enhancer.i18n import normalize_locale, tr
from voice_enhancer.infrastructure.admission import (
    AdmissionRejected,
    BoundedWriter,
    require_disk_space,
    upload_slot,
)
from voice_enhancer.infrastructure.cleanup import cleanup_deleted_media
from voice_enhancer.infrastructure.database import JobStore, make_engine
from voice_enhancer.infrastructure.ffmpeg import FFmpegProcessor
from voice_enhancer.infrastructure.queue import RedisJobQueue
from voice_enhancer.infrastructure.telegram import configure_profile, make_bot

router = Router()
ffmpeg = FFmpegProcessor(settings.ffmpeg_bin, settings.ffprobe_bin, settings.ffmpeg_timeout_seconds)
logger = logging.getLogger(__name__)


async def _track(
    store: JobStore,
    name: str,
    user_id: int,
    *,
    job_id: str | None = None,
    event_key: str | None = None,
) -> None:
    try:
        await store.record_event(name, user_id, job_id=job_id, event_key=event_key)
    except Exception:
        logger.exception("Could not record analytics event %s", name)


@router.message(CommandStart())
async def start(message: Message, store: JobStore) -> None:
    locale = normalize_locale(message.from_user.language_code if message.from_user else None)
    await message.answer(tr(locale, "start"), reply_markup=_start_keyboard(locale))
    if message.from_user is not None:
        await _track(
            store,
            "start",
            message.from_user.id,
            event_key=f"start:{message.chat.id}:{message.message_id}",
        )


@router.message(Command("delete_my_data"))
async def delete_my_data(message: Message, store: JobStore) -> None:
    if message.from_user is None:
        return
    locale = normalize_locale(message.from_user.language_code)
    await store.request_user_deletion(message.from_user.id)
    await cleanup_deleted_media(store, Path(settings.media_root))
    await message.answer(tr(locale, "data_deleted"))


def _start_keyboard(locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tr(locale, "how_button"), callback_data="info:how")],
            [InlineKeyboardButton(text=tr(locale, "pricing_button"), callback_data="info:pricing")],
        ]
    )


@router.callback_query(F.data == "info:how")
async def show_how(callback: CallbackQuery) -> None:
    locale = normalize_locale(callback.from_user.language_code)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(tr(locale, "how_text"))


@router.callback_query(F.data == "info:pricing")
async def show_pricing(callback: CallbackQuery) -> None:
    locale = normalize_locale(callback.from_user.language_code)
    await callback.answer(tr(locale, "pricing_soon"), show_alert=True)


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


def _preset_keyboard(job_id: str) -> InlineKeyboardMarkup:
    labels = {
        Preset.NATURAL: "🌿 Natural",
        Preset.STUDIO: "🎙 Studio",
        Preset.REELS: "📱 Reels",
        Preset.PODCAST: "🎧 Podcast",
    }
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=labels[preset], callback_data=f"p:{job_id}:{preset.value}")]
            for preset in Preset
        ]
    )


def result_keyboard(job_id: str, locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(locale, "another_preset_button"), callback_data=f"r:{job_id}"
                )
            ],
            [InlineKeyboardButton(text=tr(locale, "new_file_button"), callback_data="new:file")],
        ]
    )


@router.callback_query(F.data == "new:file")
async def request_new_file(callback: CallbackQuery) -> None:
    locale = normalize_locale(callback.from_user.language_code)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(tr(locale, "new_file_prompt"))


@router.callback_query(F.data.startswith("r:"))
async def reprocess_original(callback: CallbackQuery, store: JobStore) -> None:
    try:
        with upload_slot(Path(settings.media_root), "telegram", callback.from_user.id,
                         settings.max_media_size_bytes):
            await _reprocess_original(callback, store)
    except AdmissionRejected as error:
        locale = normalize_locale(callback.from_user.language_code)
        await callback.answer(tr(locale, error.code), show_alert=True)


async def _reprocess_original(callback: CallbackQuery, store: JobStore) -> None:
    locale = normalize_locale(callback.from_user.language_code)
    if callback.data is None:
        return
    original_id = callback.data.removeprefix("r:")
    original = await store.get_owned(original_id, callback.from_user.id)
    if original is None or original.status != JobStatus.COMPLETED.value:
        await callback.answer(tr(locale, "reprocess_unavailable"), show_alert=True)
        return
    media_root = Path(settings.media_root).resolve()
    source = Path(original.source_path).resolve()
    if source.parent != media_root / original.id or not source.is_file():
        await callback.answer(tr(locale, "source_expired"), show_alert=True)
        return
    job_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{original_id}:{callback.id}").hex
    target_dir = media_root / job_id
    owns_directory = False
    try:
        await store.check_capacity("telegram", callback.from_user.id)
        require_disk_space(media_root, source.stat().st_size)
        target_dir.mkdir()
        owns_directory = True
        target = target_dir / source.name
        try:
            os.link(source, target)
        except OSError:
            await asyncio.to_thread(shutil.copyfile, source, target)
        created = await store.create_reprocess_pending(
            job_id=job_id, original=original, source_path=target
        )
        if not created:
            shutil.rmtree(target_dir)
            await callback.answer(tr(locale, "reprocess_unavailable"), show_alert=True)
            return
    except AdmissionRejected as error:
        if owns_directory:
            shutil.rmtree(target_dir)
        await callback.answer(tr(locale, error.code), show_alert=True)
        return
    except FileExistsError:
        await callback.answer(tr(locale, "reprocess_unavailable"), show_alert=True)
        return
    except Exception:
        logger.exception("Could not prepare reprocessing for job %s", original_id)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        await callback.answer(tr(locale, "read_error"), show_alert=True)
        return
    await callback.answer()
    await _track(
        store,
        "reprocess_requested",
        callback.from_user.id,
        job_id=job_id,
        event_key=f"reprocess:{job_id}",
    )
    if isinstance(callback.message, Message):
        await callback.message.answer(
            tr(locale, "choose_preset"), reply_markup=_preset_keyboard(job_id)
        )


@router.message(F.video | F.audio | F.voice | F.document)
async def receive_media(message: Message, bot: Bot, store: JobStore) -> None:
    if message.from_user is None:
        return
    locale = normalize_locale(message.from_user.language_code)
    try:
        with upload_slot(Path(settings.media_root), "telegram", message.from_user.id,
                         settings.max_media_size_bytes):
            await store.check_capacity("telegram", message.from_user.id)
            async with asyncio.timeout(1800):
                await _receive_media(message, bot, store)
    except AdmissionRejected as error:
        await message.answer(tr(locale, error.code))
    except TimeoutError:
        await message.answer(tr(locale, "read_error"))


async def _receive_media(message: Message, bot: Bot, store: JobStore) -> None:
    if message.from_user is None:
        return
    locale = normalize_locale(message.from_user.language_code)
    media = _media_from_message(message)
    if media is None:
        await _track(
            store,
            "media_rejected",
            message.from_user.id,
            event_key=f"rejected:{message.chat.id}:{message.message_id}",
        )
        await message.answer(tr(locale, "unsupported_format"))
        return
    file_id, suffix, kind = media
    try:
        telegram_file = await bot.get_file(file_id)
        size = telegram_file.file_size or 0
        if size <= 0 or size > settings.max_media_size_bytes:
            await _track(
                store,
                "media_rejected",
                message.from_user.id,
                event_key=f"rejected:{message.chat.id}:{message.message_id}",
            )
            await message.answer(tr(locale, "file_too_large"))
            return
    except Exception:
        logger.exception("Could not inspect Telegram file")
        await message.answer(tr(locale, "telegram_file_error"))
        return

    media_root = Path(settings.media_root)
    media_root.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    target_dir = media_root / job_id
    target = target_dir / f"source{suffix}"
    with tempfile.TemporaryDirectory(prefix="incoming-", dir=media_root) as temp:
        downloaded = Path(temp) / f"source{suffix}"
        try:
            with BoundedWriter(downloaded, settings.max_media_size_bytes) as output:
                await bot.download(telegram_file, destination=output, timeout=1800)
            info = await ffmpeg.probe(downloaded)
            streams = info.get("streams", [])
            metadata = MediaMetadata(
                kind=kind,
                duration_seconds=float(info.get("format", {}).get("duration", 0) or 0),
                size_bytes=downloaded.stat().st_size,
                has_audio=any(s.get("codec_type") == "audio" for s in streams),
                has_video=any(s.get("codec_type") == "video" for s in streams),
            )
            validate_media(
                downloaded,
                metadata,
                max_duration_seconds=settings.max_media_duration_seconds,
                max_size_bytes=settings.max_media_size_bytes,
            )
            target_dir.mkdir()
            shutil.move(downloaded, target)
            _, created = await store.create_pending(
                job_id=job_id,
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                message_id=message.message_id,
                kind=kind.value,
                source_path=target,
                locale=locale,
            )
            if not created:
                shutil.rmtree(target_dir)
                await message.answer(tr(locale, "duplicate_file"))
                return
        except AdmissionRejected:
            if target_dir.exists():
                shutil.rmtree(target_dir)
            raise
        except MediaValidationError as error:
            await _track(
                store,
                "media_rejected",
                message.from_user.id,
                event_key=f"rejected:{message.chat.id}:{message.message_id}",
            )
            await message.answer(tr(locale, error.code))
            return
        except Exception:
            logger.exception("Media validation or storage failed")
            if target_dir.exists():
                shutil.rmtree(target_dir)
            await message.answer(tr(locale, "read_error"))
            return
    await _track(
        store, "media_received", message.from_user.id, job_id=job_id, event_key=f"received:{job_id}"
    )
    await message.answer(tr(locale, "choose_preset"), reply_markup=_preset_keyboard(job_id))


@router.callback_query(F.data.startswith("p:"))
async def process_preset(callback: CallbackQuery, store: JobStore, queue: RedisJobQueue) -> None:
    if callback.data is None:
        return
    locale = normalize_locale(callback.from_user.language_code)
    try:
        _, job_id, preset_value = callback.data.split(":", 2)
        preset = Preset(preset_value)
    except ValueError:
        await callback.answer(tr(locale, "unknown_preset"), show_alert=True)
        return
    status_message_id = (
        callback.message.message_id if isinstance(callback.message, Message) else None
    )
    accepted = await store.queue_for_preset(
        job_id, callback.from_user.id, preset.value, status_message_id
    )
    if not accepted:
        await callback.answer(tr(locale, "stale_preset"), show_alert=True)
        return
    await _track(
        store, "preset_selected", callback.from_user.id, job_id=job_id, event_key=f"preset:{job_id}"
    )
    try:
        await queue.enqueue(job_id)
    except Exception:
        # The queued row is durable; the worker reconciles jobs missing from Redis.
        logger.exception("Queue unavailable after job %s was committed", job_id)
    await callback.answer(tr(locale, "queued_ack"))
    if isinstance(callback.message, Message):
        await callback.message.edit_text(tr(locale, "queued"))


async def _run() -> None:
    logging.basicConfig(level=settings.log_level)
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    bot = make_bot(settings)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    dispatcher["store"] = JobStore(engine)
    dispatcher["queue"] = RedisJobQueue(redis)
    try:
        await configure_profile(bot)
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        await redis.aclose()
        await engine.dispose()


def run() -> None:
    asyncio.run(_run())
