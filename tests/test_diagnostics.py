import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from voice_enhancer.config import Settings
from voice_enhancer.diagnostics import HEARTBEAT_KEY, diagnose, heartbeat
from voice_enhancer.infrastructure.database import initialize_database, make_engine


@pytest.mark.asyncio
async def test_diagnostics_are_aggregate_and_credentials_never_leak(tmp_path, monkeypatch):
    config = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path / 'db'}",
                      media_root=str(tmp_path), bot_token="secret-token",
                      redis_url="redis://:secret-password@localhost:1/0")
    engine = make_engine(config.database_url)
    await initialize_database(engine)
    await engine.dispose()
    redis = AsyncMock()
    redis.ping.side_effect = RuntimeError("secret-password")
    monkeypatch.setattr("voice_enhancer.diagnostics.Redis.from_url", lambda *a, **kw: redis)
    result = await diagnose(config)
    assert result["database_ok"] and not result["redis_ok"] and not result["ready"]
    assert result["jobs_by_status"] == {}
    assert "secret" not in json.dumps(result)
    redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_heartbeat_has_expiry_and_stops_on_cancellation():
    redis = AsyncMock()
    done = asyncio.Event()
    redis.set.side_effect = lambda *a, **kw: done.set()
    task = asyncio.create_task(heartbeat(redis))
    await asyncio.wait_for(done.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    redis.set.assert_awaited_once_with(HEARTBEAT_KEY, "1", ex=30)
