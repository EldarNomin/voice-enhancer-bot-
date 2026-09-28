from pathlib import Path

import pytest

from voice_enhancer.domain.job import JobStatus
from voice_enhancer.infrastructure.database import JobStore, initialize_database, make_engine
from voice_enhancer.infrastructure.worker_lock import single_worker


@pytest.mark.asyncio
async def test_low_storage_pauses_before_claim_and_keeps_cleanup(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from voice_enhancer.infrastructure.admission import AdmissionRejected
    from voice_enhancer.worker import MediaWorker

    store, queue = AsyncMock(), AsyncMock()
    store.recover_jobs.return_value = []
    store.queued_ids.return_value = []
    cleanup = AsyncMock()
    monkeypatch.setattr("voice_enhancer.worker.cleanup_media", cleanup)
    monkeypatch.setattr("voice_enhancer.worker.time", SimpleNamespace(monotonic=lambda: 0.0))

    def no_space(_):
        raise AdmissionRejected("storage_busy")

    monkeypatch.setattr("voice_enhancer.worker.require_disk_space", no_space)
    pause = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr("voice_enhancer.worker.asyncio.sleep", pause)
    worker = MediaWorker(store=store, queue=queue, bot=AsyncMock(), processor=AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        await worker.serve()
    queue.claim.assert_not_awaited()
    cleanup.assert_awaited_once()
    pause.assert_awaited_once_with(5)


def test_second_worker_cannot_reset_active_jobs(tmp_path: Path) -> None:
    with (
        single_worker(tmp_path),
        pytest.raises(RuntimeError, match="Another worker"),
        single_worker(tmp_path),
    ):
        pytest.fail("second worker entered recovery")
    # A crash/exit releases the OS lock without deleting the lock file.
    with single_worker(tmp_path):
        assert (tmp_path / ".worker.lock").is_file()


@pytest.mark.asyncio
async def test_crash_loop_exhausts_persistent_retry_budget(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    try:
        await initialize_database(engine)
        store = JobStore(engine)
        job_id = "c" * 32
        await store.create_pending(
            job_id=job_id, chat_id=1, user_id=1, message_id=1,
            kind="audio", source_path=tmp_path / "source.wav",
        )
        await store.queue_for_preset(job_id, 1, "natural")
        for _ in range(2):
            assert await store.claim(job_id)
            assert await store.recover_jobs() == [job_id]
        assert await store.claim(job_id)
        assert await store.recover_jobs() == []
        job = await store.get(job_id)
        assert job.status == JobStatus.FAILED_FINAL.value
        assert job.error_code == "RETRY_LIMIT"
        assert not await store.claim(job_id)
    finally:
        await engine.dispose()
