"""Real services in CI; each test uses an isolated schema / queue namespace."""

import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from voice_enhancer.config import settings
from voice_enhancer.infrastructure.admission import AdmissionRejected
from voice_enhancer.infrastructure.database import JobStore, initialize_database
from voice_enhancer.infrastructure.queue import RedisJobQueue


@pytest.mark.asyncio
async def test_postgres_upgrades_legacy_schema_and_claims_once(tmp_path) -> None:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = "test_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await initialize_database(engine)
        async with engine.begin() as connection:
            for column in ("channel", "external_source_id", "external_status_id", "external_result_id"):
                await connection.execute(text(f"ALTER TABLE processing_jobs DROP COLUMN {column} CASCADE"))
            await connection.execute(text("ALTER TABLE analytics_events DROP COLUMN channel"))
            await connection.execute(text("ALTER TABLE processing_jobs DROP COLUMN locale"))
            await connection.execute(text("ALTER TABLE processing_jobs DROP COLUMN origin_job_id"))
            await connection.execute(text(
                "ALTER TABLE processing_jobs ALTER COLUMN source_message_id SET NOT NULL"
            ))
        await initialize_database(engine)
        await initialize_database(engine)
        store = JobStore(engine)
        job_id = uuid4().hex
        await store.create_pending(
            job_id=job_id, chat_id=2**40, user_id=2**41, message_id=1,
            kind="audio", source_path=tmp_path / "source.wav", locale="en",
        )
        assert await store.queue_for_preset(job_id, 2**41, "studio")
        results = await asyncio.gather(*(store.claim(job_id) for _ in range(8)))
        assert results.count(True) == 1
        assert await store.recover_jobs() == [job_id]
        original = await store.get(job_id)
        assert original.locale == "en" and original.channel == "telegram"
        max_id = uuid4().hex
        assert await store.create_pending(
            job_id=max_id, chat_id=2**40, user_id=2**41, message_id="mid.external",
            kind="audio", source_path=tmp_path / "max.ogg", channel="max",
        ) == (max_id, True)
        assert await store.get_owned(max_id, 2**41) is None
        assert await store.get_owned(max_id, 2**41, channel="max") is not None
        assert await store.create_reprocess_pending(
            job_id=uuid4().hex, original=original, source_path=tmp_path / "copy.wav"
        )
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
async def test_postgres_concurrent_admission_never_exceeds_cap(tmp_path, monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    monkeypatch.setattr(settings, "max_active_jobs", 2)
    schema = "test_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await initialize_database(engine)
        store = JobStore(engine)

        async def admit(index):
            try:
                await store.create_pending(
                    job_id=uuid4().hex, chat_id=index, user_id=index, message_id=index,
                    kind="audio", source_path=tmp_path / "source.wav",
                    channel="max" if index % 2 else "telegram",
                )
                return True
            except AdmissionRejected:
                return False

        assert sum(await asyncio.gather(*(admit(i) for i in range(12)))) == 2
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
async def test_redis_reconciliation_deduplicates_and_retry_survives_ack() -> None:
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not configured")
    client = Redis.from_url(url, decode_responses=True)
    queue = RedisJobQueue(client)
    prefix = "test:" + uuid4().hex
    queue.READY, queue.PROCESSING = prefix + ":ready", prefix + ":processing"
    try:
        await asyncio.gather(*(queue.enqueue("one") for _ in range(20)))
        assert await client.llen(queue.READY) == 1
        assert await queue.claim(timeout=1) == "one"
        await queue.enqueue("one")  # failed attempt schedules retry before ACK
        await queue.acknowledge("one")
        assert await queue.claim(timeout=1) == "one"
        await queue.recover_processing()
        assert await client.llen(queue.PROCESSING) == 0
        await queue.enqueue("one")  # PostgreSQL supplies interrupted IDs after restart
        assert await queue.claim(timeout=1) == "one"
    finally:
        await client.delete(queue.READY, queue.PROCESSING)
        await client.aclose()
