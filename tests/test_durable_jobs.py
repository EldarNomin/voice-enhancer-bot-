from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_enhancer.application.processing import ProcessedMedia
from voice_enhancer.domain.job import JobStatus
from voice_enhancer.infrastructure.database import JobStore, initialize_database, make_engine
from voice_enhancer.worker import MediaWorker


class FakeQueue:
    def __init__(self) -> None:
        self.acknowledged: list[str] = []
        self.enqueued: list[str] = []

    async def acknowledge(self, job_id: str) -> None:
        self.acknowledged.append(job_id)

    async def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)


class FakeBot:
    def __init__(self) -> None:
        self.sent = 0

    async def send_audio(self, *args, **kwargs):
        self.sent += 1
        return SimpleNamespace(message_id=123)

    async def send_video(self, *args, **kwargs):
        self.sent += 1
        return SimpleNamespace(message_id=124)


class FakeProcessor:
    async def process(self, source, *, kind, profile, on_remux=None):
        if on_remux is not None and kind.value == "video":
            await on_remux()
        return ProcessedMedia(source.parent / "enhanced.m4a", "fake", 1.0)


@pytest.mark.asyncio
async def test_job_is_unique_and_claimed_once(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    try:
        await initialize_database(engine)
        store = JobStore(engine)
        job_id, created = await store.create_pending(
            job_id="a" * 32,
            chat_id=7,
            user_id=9,
            message_id=11,
            kind="audio",
            source_path=tmp_path / "voice.ogg",
        )
        duplicate_id, duplicate_created = await store.create_pending(
            job_id="b" * 32,
            chat_id=7,
            user_id=9,
            message_id=11,
            kind="audio",
            source_path=tmp_path / "other.ogg",
        )
        assert created and not duplicate_created and duplicate_id == job_id
        assert not await store.queue_for_preset(job_id, user_id=10, preset="reels")
        assert await store.queue_for_preset(job_id, user_id=9, preset="reels")
        assert not await store.queue_for_preset(job_id, user_id=9, preset="studio")
        assert await store.claim(job_id)
        assert not await store.claim(job_id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_completes_audio_and_ignores_duplicate_delivery(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    try:
        await initialize_database(engine)
        store = JobStore(engine)
        job_id = "a" * 32
        await store.create_pending(
            job_id=job_id,
            chat_id=7,
            user_id=9,
            message_id=11,
            kind="audio",
            source_path=tmp_path / "voice.ogg",
        )
        await store.queue_for_preset(job_id, user_id=9, preset="natural")
        queue = FakeQueue()
        bot = FakeBot()
        worker = MediaWorker(store=store, queue=queue, bot=bot, processor=FakeProcessor())
        await worker.process_one(job_id)
        await worker.process_one(job_id)
        saved = await store.get(job_id)
        assert saved is not None
        assert saved.status == JobStatus.COMPLETED.value
        assert saved.result_message_id == 123
        assert bot.sent == 1
        assert queue.acknowledged == [job_id, job_id]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_restart_recovers_interrupted_job(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    try:
        await initialize_database(engine)
        store = JobStore(engine)
        job_id = "a" * 32
        await store.create_pending(
            job_id=job_id,
            chat_id=7,
            user_id=9,
            message_id=11,
            kind="video",
            source_path=tmp_path / "clip.mp4",
        )
        await store.queue_for_preset(job_id, user_id=9, preset="studio")
        await store.claim(job_id)
        assert await store.recover_jobs() == [job_id]
        saved = await store.get(job_id)
        assert saved is not None and saved.status == JobStatus.QUEUED.value
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_video_worker_passes_through_remux_state(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    try:
        await initialize_database(engine)
        store = JobStore(engine)
        job_id = "c" * 32
        await store.create_pending(
            job_id=job_id,
            chat_id=7,
            user_id=9,
            message_id=12,
            kind="video",
            source_path=tmp_path / "clip.mp4",
        )
        await store.queue_for_preset(job_id, user_id=9, preset="studio")
        bot = FakeBot()
        worker = MediaWorker(store=store, queue=FakeQueue(), bot=bot, processor=FakeProcessor())
        await worker.process_one(job_id)
        saved = await store.get(job_id)
        assert saved is not None and saved.status == JobStatus.COMPLETED.value
        assert saved.result_message_id == 124
        assert bot.sent == 1
    finally:
        await engine.dispose()
