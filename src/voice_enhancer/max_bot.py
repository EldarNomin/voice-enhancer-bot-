"""MAX webhook consumer. Telegram and MAX share jobs, presets and the media worker."""

import asyncio
import logging
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

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
from voice_enhancer.infrastructure.cleanup import cleanup_deleted_media
from voice_enhancer.infrastructure.database import JobStore, make_engine
from voice_enhancer.infrastructure.delivery import max_keyboard, max_presets
from voice_enhancer.infrastructure.ffmpeg import FFmpegError, FFmpegProcessor
from voice_enhancer.infrastructure.max_api import MaxClient
from voice_enhancer.infrastructure.max_inbox import MaxInbox
from voice_enhancer.infrastructure.queue import RedisJobQueue
from voice_enhancer.infrastructure.worker_lock import single_worker

logger = logging.getLogger(__name__)


class MaxBot:
    def __init__(
        self,
        client: MaxClient,
        store: JobStore,
        queue: RedisJobQueue,
        ffmpeg: FFmpegProcessor,
        root: Path,
    ) -> None:
        self.client, self.store, self.queue, self.ffmpeg = client, store, queue, ffmpeg
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    async def handle(self, update: dict) -> None:
        kind = update["update_type"]
        locale = normalize_locale(update.get("user_locale") or "ru")
        if kind == "message_callback":
            await self.callback(update, locale)
            return
        if kind == "bot_started":
            await self.start(update["chat_id"], locale)
            return
        if kind != "message_created":
            return
        message = update["message"]
        sender = message.get("sender") or {}
        if not sender.get("user_id") or sender.get("is_bot"):
            return
        chat_id = message["recipient"].get("chat_id")
        if chat_id is None:
            return
        user_id = sender["user_id"]
        body = message.get("body") or {}
        command = (body.get("text") or "").strip().split(" ", 1)[0]
        if command == "/start":
            await self.start(chat_id, locale)
        elif command == "/delete_my_data":
            await self.store.request_user_deletion(
                user_id, channel="max", before_timestamp=update["timestamp"]
            )
            await cleanup_deleted_media(self.store, self.root)
            await self.client.send(chat_id, tr(locale, "data_deleted"))
        else:
            attachments = body.get("attachments") or []
            media = next(
                (a for a in attachments if a.get("type") in {"audio", "video", "file"}), None
            )
            if media:
                await self.receive_media(message, media, user_id, chat_id, locale)
            else:
                await self.client.send(chat_id, tr(locale, "new_file_prompt"))

    async def start(self, chat_id: int, locale: str) -> None:
        await self.client.send(
            chat_id,
            tr(locale, "start"),
            [
                max_keyboard(
                    [
                        (tr(locale, "how_button"), "info:how"),
                        (tr(locale, "pricing_button"), "info:pricing"),
                    ]
                )
            ],
        )

    async def receive_media(
        self, message: dict, attachment: dict, user_id: int, chat_id: int, locale: str
    ) -> None:
        # Stable identity permits safe replay after a crash between DB commit and reply.
        message_id = str(message["body"]["mid"])
        job_id = uuid.uuid5(uuid.NAMESPACE_URL, f"max:{chat_id}:{message_id}").hex
        existing = await self.store.get_owned(job_id, user_id, channel="max")
        if existing:
            if existing.status == JobStatus.CREATED.value:
                await self.client.send(chat_id, tr(locale, "choose_preset"), [max_presets(job_id)])
            return
        attachment_type = attachment["type"]
        suffix = {"video": ".mp4", "audio": ".ogg"}.get(attachment_type)
        if suffix is None:
            suffix = Path(attachment.get("filename", "")).suffix.lower()
        kind = MediaKind.VIDEO if suffix in VIDEO_EXTENSIONS else MediaKind.AUDIO
        if suffix not in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS:
            await self.client.send(chat_id, tr(locale, "unsupported_format"))
            return
        # All channels use the same validation. Streaming download also enforces the cap.
        limit = min(settings.max_media_size_bytes, 250 * 1024**2)
        target_dir = self.root / job_id
        try:
            with tempfile.TemporaryDirectory(prefix="incoming-max-", dir=self.root) as temp:
                downloaded = Path(temp) / f"source{suffix}"
                url = await self.client.attachment_url(attachment)
                async with asyncio.timeout(1800):
                    await self.client.download(url, downloaded, limit)
                info = await self.ffmpeg.probe(downloaded)
                streams = info.get("streams", [])
                validate_media(
                    downloaded,
                    MediaMetadata(
                        kind,
                        float(info.get("format", {}).get("duration", 0) or 0),
                        downloaded.stat().st_size,
                        any(s.get("codec_type") == "audio" for s in streams),
                        any(s.get("codec_type") == "video" for s in streams),
                    ),
                    max_duration_seconds=settings.max_media_duration_seconds,
                    max_size_bytes=limit,
                )
                target_dir.mkdir(exist_ok=True)
                target = target_dir / downloaded.name
                os.replace(downloaded, target)
                await self.store.create_pending(
                    job_id=job_id,
                    chat_id=chat_id,
                    user_id=user_id,
                    message_id=message_id,
                    kind=kind.value,
                    source_path=target,
                    locale=locale,
                    channel="max",
                )
        except MediaValidationError as error:
            await self.client.send(chat_id, tr(locale, error.code))
            return
        except FFmpegError:
            await self.client.send(chat_id, tr(locale, "read_error"))
            return
        # Network/DB failures propagate to the durable inbox retry budget.
        await self.client.send(chat_id, tr(locale, "choose_preset"), [max_presets(job_id)])

    async def notify_failed(self, update: dict) -> None:
        locale = normalize_locale(update.get("user_locale") or "ru")
        message = update.get("message") or {}
        chat_id = (message.get("recipient") or {}).get("chat_id")
        if chat_id is not None:
            await self.client.send(chat_id, tr(locale, "failed"))

    async def callback(self, update: dict, locale: str) -> None:
        callback = update["callback"]
        callback_id = callback["callback_id"]
        user_id = callback["user"]["user_id"]
        payload = callback.get("payload") or ""
        message = update.get("message") or {}
        chat_id = (message.get("recipient") or {}).get("chat_id")
        if chat_id is None:
            await self.client.answer(callback_id, tr(locale, "stale_preset"))
            return
        if payload in {"info:how", "info:pricing", "new:file"}:
            key = {
                "info:how": "how_text",
                "info:pricing": "pricing_soon",
                "new:file": "new_file_prompt",
            }[payload]
            await self.client.answer(callback_id)
            await self.client.send(chat_id, tr(locale, key))
            return
        if payload.startswith("r:"):
            original = await self.store.get_owned(payload[2:], user_id, channel="max")
            if (
                original is None
                or original.status != JobStatus.COMPLETED.value
                or original.telegram_chat_id != chat_id
            ):
                await self.client.answer(callback_id, tr(locale, "reprocess_unavailable"))
                return
            source = Path(original.source_path).resolve()
            if source.parent != self.root / original.id or not source.is_file():
                await self.client.answer(callback_id, tr(locale, "source_expired"))
                return
            job_id = uuid.uuid5(uuid.NAMESPACE_URL, f"max:{original.id}:{callback_id}").hex
            if await self.store.get_owned(job_id, user_id, channel="max") is None:
                folder = self.root / job_id
                folder.mkdir(exist_ok=True)
                target = folder / source.name
                if not target.exists():
                    try:
                        os.link(source, target)
                    except OSError:
                        await asyncio.to_thread(shutil.copyfile, source, target)
                await self.store.create_reprocess_pending(
                    job_id=job_id, original=original, source_path=target
                )
            await self.client.answer(callback_id)
            await self.client.send(chat_id, tr(locale, "choose_preset"), [max_presets(job_id)])
            return
        try:
            prefix, job_id, preset_value = payload.split(":", 2)
            if prefix != "p":
                raise ValueError("Not a preset")
            preset = Preset(preset_value)
        except ValueError:
            await self.client.answer(callback_id, tr(locale, "unknown_preset"))
            return
        job = await self.store.get_owned(job_id, user_id, channel="max")
        if job is None or job.telegram_chat_id != chat_id:
            await self.client.answer(callback_id, tr(locale, "stale_preset"))
            return
        status_id = (message.get("body") or {}).get("mid")
        accepted = await self.store.queue_for_preset(
            job_id, user_id, preset.value, status_id, channel="max"
        )
        if not accepted:
            await self.client.answer(callback_id, tr(locale, "stale_preset"))
            return
        try:
            await self.queue.enqueue(job_id)
        except Exception:  # noqa: BLE001 -- committed job is recovered from PostgreSQL
            logger.warning("Redis unavailable; worker will reconcile job=%s", job_id)
        await self.client.answer(callback_id, tr(locale, "queued_ack"))
        if status_id:
            await self.client.edit(status_id, tr(locale, "queued"))


async def _run() -> None:
    logging.basicConfig(level=settings.log_level)
    # httpx INFO includes complete signed URLs. Keep transport logs out of normal operation.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    client = MaxClient(
        settings.max_bot_token,
        base_url=settings.max_api_base_url,
        ca_bundle=settings.max_ca_bundle,
        media_hosts=tuple(settings.max_media_hosts.split(",")),
    )
    store = JobStore(engine)
    inbox = MaxInbox(store)
    bot = MaxBot(
        client,
        store,
        RedisJobQueue(redis),
        FFmpegProcessor(settings.ffmpeg_bin, settings.ffprobe_bin, settings.ffmpeg_timeout_seconds),
        Path(settings.media_root),
    )
    try:
        await inbox.recover()
        last_cleanup = 0.0
        while True:
            if time.monotonic() - last_cleanup > 300:
                await inbox.cleanup()
                last_cleanup = time.monotonic()
            row = await inbox.claim()
            if row is None:
                await asyncio.sleep(1)
                continue
            try:
                await bot.handle(row.payload)
            except Exception as error:  # noqa: BLE001 -- retry boundary, avoid logging signed URLs
                # Exception messages may contain signed CDN URLs. Log only class + event ID.
                logger.error("MAX event failed id=%s type=%s", row.id, type(error).__name__)
                if row.attempts >= 5:
                    try:
                        await bot.notify_failed(row.payload)
                    except Exception:  # noqa: BLE001 -- final best-effort notification
                        logger.warning("MAX failure notification unavailable id=%s", row.id)
                await inbox.finish(row.id, success=False)
            else:
                await inbox.finish(row.id, success=True)
    finally:
        await client.close()
        await redis.aclose()
        await engine.dispose()


def run() -> None:
    with single_worker(Path(settings.media_root) / "max-ingress-lock"):
        asyncio.run(_run())


if __name__ == "__main__":
    run()
