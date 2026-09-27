from redis.asyncio import Redis


class RedisJobQueue:
    READY = "voice:jobs:ready"
    PROCESSING = "voice:jobs:processing"

    def __init__(self, client: Redis) -> None:
        self.client = client

    async def enqueue(self, job_id: str) -> None:
        await self.client.lpush(self.READY, job_id)

    async def claim(self, timeout: int = 5) -> str | None:
        value = await self.client.brpoplpush(self.READY, self.PROCESSING, timeout=timeout)
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else value

    async def acknowledge(self, job_id: str) -> None:
        await self.client.lrem(self.PROCESSING, 1, job_id)

    async def recover_processing(self) -> None:
        # Safe at startup with exactly one worker. Duplicate IDs are ignored by the database claim.
        for job_id in await self.client.lrange(self.PROCESSING, 0, -1):
            await self.client.lrem(self.PROCESSING, 1, job_id)
