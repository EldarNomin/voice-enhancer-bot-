import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path

from aiogram import Bot
from redis.asyncio import Redis

from voice_enhancer.application.media_validation import MediaKind
from voice_enhancer.application.processing import MediaProcessingService
from voice_enhancer.application.quality import check_media
from voice_enhancer.config import settings
from voice_enhancer.domain.job import JobStatus
from voice_enhancer.domain.profile import Preset, profile_for
from voice_enhancer.i18n import tr
from voice_enhancer.infrastructure.cleanup import cleanup_deleted_media, cleanup_media
from voice_enhancer.infrastructure.database import JobStore, make_engine
from voice_enhancer.infrastructure.delivery import Delivery, MaxDelivery, TelegramDelivery
from voice_enhancer.infrastructure.ffmpeg import FFmpegProcessor
from voice_enhancer.infrastructure.max_api import MaxClient
from voice_enhancer.infrastructure.providers import select_provider
from voice_enhancer.infrastructure.queue import RedisJobQueue
from voice_enhancer.infrastructure.telegram import make_bot
from voice_enhancer.infrastructure.worker_lock import single_worker

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


class MediaWorker:
    def __init__(
        self,
        *,
        store: JobStore,
        queue: RedisJobQueue,
        bot: Bot | None = None,
        deliveries: dict[str, Delivery] | None = None,
        processor: MediaProcessingService,
        quality_checker: Callable[[Path, Path, MediaKind], Awaitable[dict]] | None = None,
    ) -> None:
        self.store = store
        self.queue = queue
        self.deliveries = dict(deliveries or {})
        if bot is not None:
            self.deliveries["telegram"] = TelegramDelivery(bot)
        self.processor = processor
        self.quality_checker = quality_checker

    async def process_one(self, job_id: str) -> None:
        if not await self.store.claim(job_id):
            await self.queue.acknowledge(job_id)
            return
        job = await self.store.get(job_id)
        if job is None:
            await self.queue.acknowledge(job_id)
            await cleanup_deleted_media(self.store, Path(settings.media_root))
            return
        try:
            kind = MediaKind(job.kind)
            await self._track("full_processing_started", job)
            await self._update_status(job, tr(job.locale, "processing"))

            async def mark_remuxing() -> None:
                if not await self.store.transition(
                    job_id, JobStatus.PROCESSING, JobStatus.REMUXING
                ):
                    raise RuntimeError("Job state changed while remuxing")
                await self._update_status(job, tr(job.locale, "remuxing"))

            result = await self.processor.process(
                Path(job.source_path),
                kind=kind,
                profile=profile_for(Preset(job.preset)),
                on_remux=mark_remuxing,
            )
            if self.quality_checker is not None:
                checks = await self.quality_checker(Path(job.source_path), result.output_path, kind)
                logger.info(
                    "Media quality checked job=%s duration_drift_ms=%s sample_peak_dbfs=%s",
                    job_id,
                    checks["duration_drift_ms"],
                    checks["sample_peak_dbfs"],
                )
            previous = JobStatus.REMUXING if kind is MediaKind.VIDEO else JobStatus.PROCESSING
            if not await self.store.transition(
                job_id, previous, JobStatus.UPLOADING, output_path=result.output_path
            ):
                raise RuntimeError("Job state changed before upload")
            input_bytes = Path(job.source_path).stat().st_size
            output_bytes = result.output_path.stat().st_size
            await self.store.record_cost(
                job_id,
                provider=result.provider,
                provider_cost_usd=result.provider_cost_usd,
                compute_seconds=result.compute_seconds,
                storage_bytes=input_bytes + output_bytes,
                telegram_bytes_in=input_bytes,
                telegram_bytes_out=output_bytes,
            )
            delivery = self.deliveries[job.channel]
            message_id = await delivery.send_result(job, result.output_path)
            if not await self.store.transition(
                job_id,
                JobStatus.UPLOADING,
                JobStatus.COMPLETED,
                result_message_id=int(message_id) if job.channel == "telegram" else None,
                external_result_id=message_id,
            ):
                raise RuntimeError("Job state changed after upload")
            await self._update_status(job, tr(job.locale, "completed"))
            await self._track("full_processing_completed", job)
            logger.info("Job completed: %s", job_id)
        except Exception:
            logger.exception("Job failed: %s", job_id)
            await self._handle_failure(job_id, job.attempts)
        finally:
            await self.queue.acknowledge(job_id)
            await cleanup_deleted_media(self.store, Path(settings.media_root))

    async def _update_status(self, job, text: str) -> None:
        try:
            await self.deliveries[job.channel].update_status(job, text)
        except Exception:
            logger.exception("Could not update progress message for job %s", job.id)

    async def _track(self, name: str, job) -> None:
        try:
            await self.store.record_event(
                name,
                job.telegram_user_id,
                job_id=job.id,
                event_key=f"{name}:{job.id}",
                channel=job.channel,
            )
        except Exception:
            logger.exception("Could not record analytics event %s for job %s", name, job.id)

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
            await self._track("full_processing_failed", current)
            await self._update_status(current, tr(current.locale, "failed"))

    async def serve(self) -> None:
        await self.queue.recover_processing()
        for job_id in await self.store.recover_jobs(MAX_ATTEMPTS):
            await self.queue.enqueue(job_id)
        last_cleanup = 0.0
        last_reconcile = 0.0
        while True:
            if time.monotonic() - last_reconcile >= 30:
                # Reconcile even under sustained load, not only when Redis is empty.
                for missing_id in await self.store.queued_ids():
                    await self.queue.enqueue(missing_id)
                last_reconcile = time.monotonic()
            if time.monotonic() - last_cleanup >= 300:
                await cleanup_media(self.store, Path(settings.media_root))
                last_cleanup = time.monotonic()
            job_id = await self.queue.claim(timeout=5)
            if job_id is not None:
                await self.process_one(job_id)


async def _run() -> None:
    logging.basicConfig(level=settings.log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    bot = make_bot(settings) if settings.bot_token else None
    max_client = (
        MaxClient(
            settings.max_bot_token,
            base_url=settings.max_api_base_url,
            ca_bundle=settings.max_ca_bundle,
            media_hosts=tuple(settings.max_media_hosts.split(",")),
        )
        if settings.max_bot_token
        else None
    )
    deliveries = {"max": MaxDelivery(max_client)} if max_client else {}
    if bot is None and max_client is None:
        raise ValueError("BOT_TOKEN or MAX_BOT_TOKEN is required")
    ffmpeg = FFmpegProcessor(
        settings.ffmpeg_bin, settings.ffprobe_bin, settings.ffmpeg_timeout_seconds
    )
    try:
        await MediaWorker(
            store=JobStore(engine),
            queue=RedisJobQueue(redis),
            bot=bot,
            deliveries=deliveries,
            processor=MediaProcessingService(
                ffmpeg,
                provider=select_provider(
                    settings.enhancement_provider,
                    elevenlabs_api_key=settings.elevenlabs_api_key,
                    deepfilter_bin=settings.deepfilter_bin,
                    gtcrn_model=settings.gtcrn_model,
                ),
            ),
            quality_checker=partial(check_media, ffmpeg),
        ).serve()
    finally:
        if bot is not None:
            await bot.session.close()
        if max_client is not None:
            await max_client.close()
        await redis.aclose()
        await engine.dispose()


def run() -> None:
    with single_worker(Path(settings.media_root)):
        asyncio.run(_run())


if __name__ == "__main__":
    run()
