from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_enhancer.application.media_validation import MediaValidationError
from voice_enhancer.config import settings
from voice_enhancer.infrastructure.admission import (
    AdmissionRejected,
    BoundedWriter,
    require_disk_space,
    upload_slot,
)
from voice_enhancer.infrastructure.database import JobStore, initialize_database, make_engine


def test_slots_are_shared_between_channels_and_released(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_uploads", 1)
    monkeypatch.setattr("shutil.disk_usage", lambda _: SimpleNamespace(free=10**12))
    with (upload_slot(tmp_path, "telegram", 1, 100), pytest.raises(AdmissionRejected),
          upload_slot(tmp_path, "max", 2, 100)):
        pytest.fail("Second upload acquired the only slot")
    with pytest.raises(ValueError), upload_slot(tmp_path, "telegram", 1, 100):
        raise ValueError("Interrupted download")
    with upload_slot(tmp_path, "max", 2, 100):
        pass


def test_same_user_cannot_upload_twice(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda _: SimpleNamespace(free=10**12))
    with (upload_slot(tmp_path, "telegram", 1, 100), pytest.raises(AdmissionRejected),
          upload_slot(tmp_path, "telegram", 1, 100)):
        pytest.fail("Concurrent uploads for one user")


def test_disk_reserve_and_stream_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(AdmissionRejected, match="storage_busy"):
        require_disk_space(tmp_path)
    path = tmp_path / "bounded"
    with BoundedWriter(path, 4) as output:
        output.write(b"123")
        with pytest.raises(MediaValidationError):
            output.write(b"45")
        output.write(b"4")
    assert path.read_bytes() == b"1234"


@pytest.mark.asyncio
async def test_database_limits_include_reprocess_but_isolate_channel_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_active_jobs_per_user", 1)
    monkeypatch.setattr(settings, "max_active_jobs", 2)
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    try:
        await initialize_database(engine)
        store = JobStore(engine)

        async def create(job, channel="telegram", user=1):
            return await store.create_pending(
                job_id=job, chat_id=1, user_id=user, message_id=1,
                source_path=Path("source.wav"), kind="audio", channel=channel,
            )

        assert await create("first") == ("first", True)
        assert await create("duplicate") == ("first", False)
        with pytest.raises(AdmissionRejected, match="too_many_jobs"):
            await store.check_capacity("telegram", 1)
        original = await store.get("first")
        with pytest.raises(AdmissionRejected, match="too_many_jobs"):
            await store.create_reprocess_pending(
                job_id="reprocess", original=original, source_path=Path("copy.wav")
            )
        assert await create("second", "max") == ("second", True)
        with pytest.raises(AdmissionRejected, match="server_busy"):
            await store.check_capacity("telegram", 2)
        await store.request_user_deletion(1, channel="telegram")
        await store.check_capacity("telegram", 1)
    finally:
        await engine.dispose()
