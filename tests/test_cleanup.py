from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from voice_enhancer.domain.job import JobStatus
from voice_enhancer.infrastructure.cleanup import cleanup_deleted_media, cleanup_media
from voice_enhancer.infrastructure.database import (
    JobRow,
    JobStore,
    initialize_database,
    make_engine,
)


@pytest.mark.asyncio
async def test_source_and_result_have_distinct_retention_windows(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    try:
        await initialize_database(engine)
        store = JobStore(engine)
        root = tmp_path / "media"
        job_id = "a" * 32
        folder = root / job_id
        folder.mkdir(parents=True)
        source = folder / "source.ogg"
        result = folder / "enhanced.m4a"
        source.write_bytes(b"input")
        result.write_bytes(b"output")
        await store.create_pending(
            job_id=job_id,
            chat_id=1,
            user_id=2,
            message_id=3,
            kind="audio",
            source_path=source,
        )
        now = datetime.now(UTC)
        async with store.sessions.begin() as session:
            await session.execute(
                update(JobRow)
                .where(JobRow.id == job_id)
                .values(
                    status=JobStatus.COMPLETED.value,
                    updated_at=now - timedelta(hours=25),
                )
            )
        await cleanup_media(store, root, now)
        assert not source.exists() and result.exists()
        async with store.sessions.begin() as session:
            await session.execute(
                update(JobRow)
                .where(JobRow.id == job_id)
                .values(updated_at=now - timedelta(hours=73))
            )
        await cleanup_media(store, root, now)
        assert not folder.exists()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unselected_upload_expires_after_30_minutes(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    try:
        await initialize_database(engine)
        store = JobStore(engine)
        root = tmp_path / "media"
        job_id = "b" * 32
        folder = root / job_id
        folder.mkdir(parents=True)
        source = folder / "source.ogg"
        source.write_bytes(b"input")
        await store.create_pending(
            job_id=job_id,
            chat_id=1,
            user_id=2,
            message_id=3,
            kind="audio",
            source_path=source,
        )
        now = datetime.now(UTC)
        async with store.sessions.begin() as session:
            await session.execute(
                update(JobRow)
                .where(JobRow.id == job_id)
                .values(created_at=now - timedelta(minutes=31))
            )
        await cleanup_media(store, root, now)
        saved = await store.get(job_id)
        assert saved is not None and saved.status == JobStatus.CANCELLED.value
        assert not folder.exists()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_user_deletion_removes_only_owned_history_and_media(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    try:
        await initialize_database(engine)
        store = JobStore(engine)
        root = tmp_path / "media"
        first_id, other_id = "a" * 32, "b" * 32
        for job_id, user_id in ((first_id, 9), (other_id, 10)):
            folder = root / job_id
            folder.mkdir(parents=True)
            source = folder / "source.ogg"
            source.write_bytes(b"private")
            await store.create_pending(
                job_id=job_id,
                chat_id=user_id,
                user_id=user_id,
                message_id=1,
                kind="audio",
                source_path=source,
            )
        await store.record_cost(
            first_id,
            provider="ffmpeg",
            provider_cost_usd=0,
            compute_seconds=1,
            storage_bytes=7,
            telegram_bytes_in=7,
            telegram_bytes_out=0,
        )
        await store.record_event("media_received", 9, job_id=first_id)
        assert await store.request_user_deletion(9) == [first_id]
        assert await store.get(first_id) is None
        assert await store.get_cost(first_id) is None
        assert await store.events_for_user(9) == []
        assert await store.get(other_id) is not None
        assert await store.pending_media_deletions() == [first_id]
        await cleanup_deleted_media(store, root)
        assert not (root / first_id).exists()
        assert (root / other_id).exists()
        assert await store.pending_media_deletions() == []
    finally:
        await engine.dispose()
