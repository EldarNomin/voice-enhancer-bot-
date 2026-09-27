import asyncio
import logging
import time
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile
from redis.asyncio import Redis

from voice_enhancer.application.media_validation import MediaKind
from voice_enhancer.application.processing import MediaProcessingService
from voice_enhancer.config import settings
from voice_enhancer.domain.job import JobStatus
from voice_enhancer.domain.profile import Preset, profile_for
from voice_enhancer.infrastructure.cleanup import cleanup_media
from voice_enhancer.infrastructure.database import JobStore, make_engine
from voice_enhancer.infrastructure.ffmpeg import FFmpegProcessor
from voice_enhancer.infrastructure.providers import select_provider
from voice_enhancer.infrastructure.queue import RedisJobQueue
from voice_enhancer.infrastructure.telegram import make_bot

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


class MediaWorker:
    def __init__(
        self,
        *,
        store: JobStore,
        queue: RedisJobQueue,
        bot: Bot,
        processor: MediaProcessingService,
    ) -> None:
        self.store = store
        self.queue = queue
        self.bot = bot
        self.processor = processor

    async def process_one(self, job_id: str) -> None:
        if not await self.store.claim(job_id):
            await self.queue.acknowledge(job_id)
            return
        job = await self.store.get(job_id)
        assert job is not None
        try:
            kind = MediaKind(job.kind)
            await self._update_status(job, "Обрабатываю голос…")

            async def mark_remuxing() -> None:
                if not await self.store.transition(
                    job_id, JobStatus.PROCESSING, JobStatus.REMUXING
                ):
                    raise RuntimeError("Job state changed while remuxing")
                await self._update_status(job, "Собираю видео…")

            result = await self.processor.process(
                Path(job.source_path),
                kind=kind,
                profile=profile_for(Preset(job.preset)),
                on_remux=mark_remuxing,
            )
            previous = JobStatus.REMUXING if kind is MediaKind.VIDEO else JobStatus.PROCESSING
            if not await self.store.transition(
                job_id, previous, JobStatus.UPLOADING, output_path=result.output_path
            ):
                raise RuntimeError("Job state changed before upload")
            upload = FSInputFile(result.output_path, filename=result.output_path.name)
            if kind is MediaKind.VIDEO:
                message = await self.bot.send_video(
                    job.telegram_chat_id,
                    upload,
                    caption="Готово! Видео с обработанным голосом.",
                    request_timeout=3600,
                )
            else:
                message = await self.bot.send_audio(
                    job.telegram_chat_id,
                    upload,
                    caption="Готово! Аудио с обработанным голосом.",
                    request_timeout=3600,
                )
            if not await self.store.transition(
                job_id,
                JobStatus.UPLOADING,
                JobStatus.COMPLETED,
                result_message_id=message.message_id,
            ):
                raise RuntimeError("Job state changed after upload")
            await self._update_status(job, "Готово! Результат отправлен.")
            logger.info("Job completed: %s", job_id)
        except Exception:
            logger.exception("Job failed: %s", job_id)
            await self._handle_failure(job_id, job.attempts)
        finally:
            await self.queue.acknowledge(job_id)

    async def _update_status(self, job, text: str) -> None:
        if job.status_message_id is None:
            return
        try:
            await self.bot.edit_message_text(
                text=text,
                chat_id=job.telegram_chat_id,
                message_id=job.status_message_id,
            )
        except Exception:
            logger.exception("Could not update progress message for job %s", job.id)

    async def _handle_failure(self, job_id: str, attempts: int) -> None:
        current = await self.store.get(job_id)
        if current is None or JobStatus(current.status) not in {
            JobStatus.PROCESSING,
            JobStatus.REMUXING,
            JobStatus.UPLOADING,
        }:
            return
        target = JobStatus.FAILED_RETRYABLE if attempts < MAX_ATTEMPTS else JobStatus.FAILED_FINAL
        if not await self.store.transition(
            job_id, JobStatus(current.status), target, error_code="PROCESSING_ERROR"
        ):
            return
        if target is JobStatus.FAILED_RETRYABLE:
            await self.store.transition(job_id, target, JobStatus.QUEUED)
            await self.queue.enqueue(job_id)
        else:
            await self._update_status(
                current, "Не удалось обработать файл. Пришли его ещё раз чуть позже."
            )

    async def serve(self) -> None:
        await self.queue.recover_processing()
        for job_id in await self.store.recover_jobs():
            await self.queue.enqueue(job_id)
        last_cleanup = 0.0
        while True:
            if time.monotonic() - last_cleanup >= 300:
                await cleanup_media(self.store, Path(settings.media_root))
                last_cleanup = time.monotonic()
            job_id = await self.queue.claim(timeout=5)
            if job_id is not None:
                await self.process_one(job_id)
            else:
                # Repair a database commit that happened just before bot/Redis lost connection.
                for missing_id in await self.store.queued_ids():
                    await self.queue.enqueue(missing_id)


async def _run() -> None:
    logging.basicConfig(level=settings.log_level)
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    bot = make_bot(settings)
    try:
        await MediaWorker(
            store=JobStore(engine),
            queue=RedisJobQueue(redis),
            bot=bot,
            processor=MediaProcessingService(
                FFmpegProcessor(settings.ffmpeg_bin, settings.ffprobe_bin),
                provider=select_provider(
                    settings.enhancement_provider,
                    elevenlabs_api_key=settings.elevenlabs_api_key,
                ),
            ),
        ).serve()
    finally:
        await bot.session.close()
        await redis.aclose()
        await engine.dispose()


def run() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    run()
