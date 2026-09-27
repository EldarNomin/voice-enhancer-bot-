import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_enhancer.application.processing import ProcessedMedia
from voice_enhancer.bot import reprocess_original
from voice_enhancer.config import settings
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
        self.captions: list[str] = []

    async def send_audio(self, *args, **kwargs):
        self.sent += 1
        self.captions.append(kwargs["caption"])
        return SimpleNamespace(message_id=123)

    async def send_video(self, *args, **kwargs):
        self.sent += 1
        self.captions.append(kwargs["caption"])
        return SimpleNamespace(message_id=124)


class FakeProcessor:
    async def process(self, source, *, kind, profile, on_remux=None):
        if on_remux is not None and kind.value == "video":
            await on_remux()
        output = source.parent / "enhanced.m4a"
        output.write_bytes(b"output-data")
        return ProcessedMedia(output, "fake", 1.0)


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
async def test_reprocess_job_has_new_identity_and_keeps_original(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    try:
        await initialize_database(engine)
        store = JobStore(engine)
        original_id = "a" * 32
        source = tmp_path / "source.mp4"
        source.write_bytes(b"original")
        await store.create_pending(
            job_id=original_id,
            chat_id=7,
            user_id=9,
            message_id=11,
            kind="video",
            source_path=source,
        )
        original = await store.get_owned(original_id, 9)
        assert original is not None
        assert await store.get_owned(original_id, 10) is None
        new_path = tmp_path / "copy.mp4"
        new_path.write_bytes(source.read_bytes())
        new_id = "b" * 32
        assert await store.create_reprocess_pending(
            job_id=new_id, original=original, source_path=new_path
        )
        assert not await store.create_reprocess_pending(
            job_id=new_id, original=original, source_path=new_path
        )
        new_job = await store.get(new_id)
        assert new_job is not None
        assert new_job.origin_job_id == original_id
        assert new_job.source_message_id is None
        assert new_job.status == JobStatus.CREATED.value
        assert source.read_bytes() == b"original"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reprocess_callback_copies_owned_completed_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_root = tmp_path / "media"
    original_id = "a" * 32
    source_dir = media_root / original_id
    source_dir.mkdir(parents=True)
    source = source_dir / "source.mp4"
    source.write_bytes(b"video-data")
    monkeypatch.setattr(settings, "media_root", str(media_root))
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    try:
        await initialize_database(engine)
        store = JobStore(engine)
        await store.create_pending(
            job_id=original_id,
            chat_id=7,
            user_id=9,
            message_id=11,
            kind="video",
            source_path=source,
        )
        await store.queue_for_preset(original_id, user_id=9, preset="studio")
        await store.claim(original_id)
        await store.transition(original_id, JobStatus.PROCESSING, JobStatus.UPLOADING)
        await store.transition(original_id, JobStatus.UPLOADING, JobStatus.COMPLETED)
        answers: list[str | None] = []

        async def answer(text: str | None = None, **kwargs) -> None:
            answers.append(text)

        callback = SimpleNamespace(
            data=f"r:{original_id}",
            id="callback-1",
            from_user=SimpleNamespace(id=9, language_code="ru"),
            message=None,
            answer=answer,
        )
        await reprocess_original(callback, store)
        new_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{original_id}:callback-1").hex
        new_job = await store.get(new_id)
        assert new_job is not None and new_job.origin_job_id == original_id
        assert Path(new_job.source_path).read_bytes() == b"video-data"
        assert source.read_bytes() == b"video-data"
        assert answers == [None]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_deletion_during_processing_prevents_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "media"
    job_id = "d" * 32
    folder = root / job_id
    folder.mkdir(parents=True)
    source = folder / "source.ogg"
    source.write_bytes(b"input")
    monkeypatch.setattr(settings, "media_root", str(root))
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    started = asyncio.Event()
    release = asyncio.Event()

    class WaitingProcessor:
        async def process(self, source, *, kind, profile, on_remux=None):
            started.set()
            await release.wait()
            return ProcessedMedia(source, "fake", 1.0)

    try:
        await initialize_database(engine)
        store = JobStore(engine)
        await store.create_pending(
            job_id=job_id, chat_id=7, user_id=9, message_id=11, kind="audio", source_path=source
        )
        await store.queue_for_preset(job_id, user_id=9, preset="natural")
        bot = FakeBot()
        queue = FakeQueue()
        worker = MediaWorker(store=store, queue=queue, bot=bot, processor=WaitingProcessor())
        task = asyncio.create_task(worker.process_one(job_id))
        await asyncio.wait_for(started.wait(), timeout=2)
        await store.request_user_deletion(9)
        release.set()
        await asyncio.wait_for(task, timeout=2)
        assert bot.sent == 0
        assert await store.get(job_id) is None
        assert not folder.exists()
        assert queue.acknowledged == [job_id]
    finally:
        release.set()
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_completes_audio_and_ignores_duplicate_delivery(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    try:
        await initialize_database(engine)
        store = JobStore(engine)
        job_id = "a" * 32
        (tmp_path / "voice.ogg").write_bytes(b"input")
        await store.create_pending(
            job_id=job_id,
            chat_id=7,
            user_id=9,
            message_id=11,
            kind="audio",
            source_path=tmp_path / "voice.ogg",
            locale="en",
        )
        await store.queue_for_preset(job_id, user_id=9, preset="natural")
        queue = FakeQueue()
        bot = FakeBot()
        checked: list[tuple[Path, Path]] = []

        async def check(source: Path, result: Path, kind) -> dict:
            checked.append((source, result))
            return {"duration_drift_ms": 0, "sample_peak_dbfs": -1.0}

        worker = MediaWorker(
            store=store, queue=queue, bot=bot, processor=FakeProcessor(), quality_checker=check
        )
        await worker.process_one(job_id)
        await worker.process_one(job_id)
        saved = await store.get(job_id)
        assert saved is not None
        assert saved.status == JobStatus.COMPLETED.value
        assert saved.result_message_id == 123
        assert bot.sent == 1
        assert checked == [(tmp_path / "voice.ogg", tmp_path / "enhanced.m4a")]
        assert bot.captions == ["Done! Here is your audio with enhanced voice."]
        assert queue.acknowledged == [job_id, job_id]
        cost = await store.get_cost(job_id)
        assert cost is not None
        assert cost.compute_seconds == 1.0
        assert cost.storage_bytes == len(b"input") + len(b"output-data")
        assert cost.telegram_bytes_out == len(b"output-data")
        assert [event.event_name for event in await store.events_for_user(9)] == [
            "full_processing_started",
            "full_processing_completed",
        ]
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
async def test_event_key_prevents_duplicate_analytics(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    try:
        await initialize_database(engine)
        store = JobStore(engine)
        assert await store.record_event("start", 9, event_key="start:9:1")
        assert not await store.record_event("start", 9, event_key="start:9:1")
        assert len(await store.events_for_user(9)) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_video_worker_passes_through_remux_state(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    try:
        await initialize_database(engine)
        store = JobStore(engine)
        job_id = "c" * 32
        (tmp_path / "clip.mp4").write_bytes(b"input-video")
        await store.create_pending(
            job_id=job_id,
            chat_id=7,
            user_id=9,
            message_id=12,
            kind="video",
            source_path=tmp_path / "clip.mp4",
            locale="ru",
        )
        await store.queue_for_preset(job_id, user_id=9, preset="studio")
        bot = FakeBot()
        worker = MediaWorker(store=store, queue=FakeQueue(), bot=bot, processor=FakeProcessor())
        await worker.process_one(job_id)
        saved = await store.get(job_id)
        assert saved is not None and saved.status == JobStatus.COMPLETED.value
        assert saved.result_message_id == 124
        assert bot.sent == 1
        assert bot.captions == ["Готово! Видео с обработанным голосом."]
    finally:
        await engine.dispose()
