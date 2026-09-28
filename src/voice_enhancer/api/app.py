import asyncio
import hmac
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from voice_enhancer.config import settings
from voice_enhancer.infrastructure.database import JobStore, make_engine
from voice_enhancer.infrastructure.max_inbox import SUPPORTED_UPDATES, MaxInbox, event_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = make_engine(settings.database_url)
    app.state.max_inbox = MaxInbox(JobStore(engine))
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="Voice Enhancer Bot API", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhooks/max")
async def max_webhook(request: Request) -> dict[str, bool]:
    if not settings.max_bot_token or len(settings.max_webhook_secret) < 32:
        raise HTTPException(503, "MAX webhook is not configured")
    supplied = request.headers.get("X-Max-Bot-Api-Secret", "")
    if not hmac.compare_digest(supplied.encode(), settings.max_webhook_secret.encode()):
        raise HTTPException(403, "Invalid webhook secret")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1024 * 1024:
            raise HTTPException(413, "Update too large")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise TypeError("Expected object")
        if payload.get("update_type") not in SUPPORTED_UPDATES:
            return {"ok": True}
        event_key(payload)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "Invalid update") from None
    # A failed commit returns 5xx, letting MAX redeliver. No background-task ACK.
    async with asyncio.timeout(20):
        await request.app.state.max_inbox.accept(payload)
    return {"ok": True}
