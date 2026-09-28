"""Read-only, aggregate operational checks. Never emit URLs, IDs or exception text."""

import asyncio
import json
import shutil

from redis.asyncio import Redis
from sqlalchemy import func, select

from voice_enhancer.config import Settings
from voice_enhancer.infrastructure.database import JobRow, JobStore, make_engine
from voice_enhancer.infrastructure.queue import RedisJobQueue

HEARTBEAT_KEY = "voice:worker:alive"


async def heartbeat(redis: Redis) -> None:
    while True:
        await redis.set(HEARTBEAT_KEY, "1", ex=30)
        await asyncio.sleep(10)


async def diagnose(config: Settings) -> dict:
    result = {"database_ok": False, "redis_ok": False, "worker_alive": False}
    engine = make_engine(config.database_url)
    redis = Redis.from_url(config.redis_url, socket_connect_timeout=3, socket_timeout=3)
    try:
        try:
            async with asyncio.timeout(5), JobStore(engine).sessions() as session:
                rows = await session.execute(
                    select(JobRow.status, func.count()).group_by(JobRow.status)
                )
                # Allow only known states in output, even if the database is corrupted.
                from voice_enhancer.domain.job import JobStatus

                allowed = {state.value for state in JobStatus}
                result["jobs_by_status"] = {state: count for state, count in rows if state in allowed}
                result["database_ok"] = True
        except Exception:  # noqa: BLE001 - diagnostics must not expose credential-bearing errors
            result["database_ok"] = False
        try:
            async with asyncio.timeout(5):
                await redis.ping()
                result["ready_queue"] = await redis.llen(RedisJobQueue.READY)
                result["processing_queue"] = await redis.llen(RedisJobQueue.PROCESSING)
                result["worker_alive"] = bool(await redis.exists(HEARTBEAT_KEY))
                result["redis_ok"] = True
        except Exception:  # noqa: BLE001 - report availability, not connection details
            result["redis_ok"] = False
        try:
            result["disk_free_bytes"] = shutil.disk_usage(config.media_root).free
        except OSError:
            result["disk_free_bytes"] = None
        reserve = (config.min_free_disk_bytes + config.max_media_size_bytes
                   + config.max_media_duration_seconds * 48000 * 2 * 4 * 3)
        result["worker_storage_ready"] = (
            result["disk_free_bytes"] is not None and result["disk_free_bytes"] >= reserve
        )
        result["ready"] = all(result[key] for key in (
            "database_ok", "redis_ok", "worker_alive", "worker_storage_ready"
        ))
        return result
    finally:
        await redis.aclose()
        await engine.dispose()


def main() -> None:
    result = asyncio.run(diagnose(Settings()))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ready"] else 1)


if __name__ == "__main__":
    main()
