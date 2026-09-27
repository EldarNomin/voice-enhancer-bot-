from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from voice_enhancer.domain.job import JobStatus
from voice_enhancer.infrastructure.cleanup import cleanup_media
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
