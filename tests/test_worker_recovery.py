from pathlib import Path

import pytest

from voice_enhancer.domain.job import JobStatus
from voice_enhancer.infrastructure.database import JobStore, initialize_database, make_engine
from voice_enhancer.infrastructure.worker_lock import single_worker


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
