import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, update

from voice_enhancer.api.app import app
from voice_enhancer.application.media_validation import MediaValidationError
from voice_enhancer.config import settings
from voice_enhancer.domain.job import JobStatus
from voice_enhancer.infrastructure.database import JobStore, initialize_database, make_engine
from voice_enhancer.infrastructure.delivery import MaxDelivery
from voice_enhancer.infrastructure.max_api import MaxClient
from voice_enhancer.infrastructure.max_inbox import MaxInbox, MaxInboxRow
from voice_enhancer.max_bot import MaxBot
from voice_enhancer.worker import MediaWorker


@pytest.fixture
async def store(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    await initialize_database(engine)
    try:
        yield JobStore(engine)
    finally:
        await engine.dispose()


def event(mid="mid.abc-123", user=9):
    return {
        "update_type": "message_created",
        "timestamp": 12345,
        "message": {
            "sender": {"user_id": user, "is_bot": False},
            "recipient": {"chat_id": 7, "chat_type": "dialog"},
            "body": {
                "mid": mid,
                "text": "",
                "attachments": [
                    {
                        "type": "audio",
                        "payload": {"url": "https://cdn.max.ru/voice", "token": "reuse"},
                    }
                ],
            },
        },
    }


@pytest.mark.asyncio
async def test_channels_isolate_ownership_queue_deletion_and_events(store, tmp_path):
    for channel, identifier in [("telegram", "a" * 32), ("max", "b" * 32)]:
        await store.create_pending(
            job_id=identifier,
            chat_id=7,
            user_id=9,
            message_id=11,
            kind="audio",
            source_path=tmp_path / "a.wav",
            channel=channel,
        )
        await store.record_event("start", 9, event_key="same", channel=channel)
    assert await store.get_owned("b" * 32, 9) is None
    assert await store.get_owned("a" * 32, 9, channel="max") is None
    assert not await store.queue_for_preset("b" * 32, 9, "natural")
    assert await store.queue_for_preset("b" * 32, 9, "natural", "mid.status", channel="max")
    assert await store.request_user_deletion(9) == ["a" * 32]
    assert await store.get("b" * 32) is not None
    assert len(await store.events_for_user(9, channel="max")) == 1
    assert not await store.events_for_user(9)


@pytest.mark.asyncio
async def test_max_string_id_dedup_and_reprocessing_channel(store, tmp_path):
    args = {
        "chat_id": 7,
        "user_id": 9,
        "message_id": "mid.non-numeric",
        "kind": "audio",
        "source_path": tmp_path / "source.ogg",
        "channel": "max",
    }
    assert await store.create_pending(job_id="a" * 32, **args) == ("a" * 32, True)
    assert await store.create_pending(job_id="b" * 32, **args) == ("a" * 32, False)
    original = await store.get("a" * 32)
    assert original.source_message_id is None
    assert await store.create_reprocess_pending(
        job_id="c" * 32, original=original, source_path=tmp_path / "copy.ogg"
    )
    assert (await store.get("c" * 32)).channel == "max"


@pytest.mark.asyncio
async def test_webhook_authenticated_durable_dedup_and_recovery(store, monkeypatch):
    monkeypatch.setattr(settings, "max_bot_token", "test")
    monkeypatch.setattr(settings, "max_webhook_secret", "s" * 32)
    inbox = MaxInbox(store)
    monkeypatch.setattr(app.state, "max_inbox", inbox, raising=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/webhooks/max", json=event())).status_code == 403
        headers = {"X-Max-Bot-Api-Secret": "s" * 32}
        for _ in range(2):
            assert (
                await client.post("/webhooks/max", json=event(), headers=headers)
            ).status_code == 200
        assert (await client.post("/webhooks/max", json=[], headers=headers)).status_code == 400
        assert (
            await client.post("/webhooks/max", content=b"x" * (1024**2 + 1), headers=headers)
        ).status_code == 413
    row = await inbox.claim()
    assert row.attempts == 1
    assert await inbox.claim() is None
    await inbox.recover()
    recovered = await inbox.claim()
    assert recovered.id == row.id and recovered.attempts == 2
    await inbox.finish(row.id, success=True)
    assert not await inbox.accept(event())
    async with store.sessions() as session:
        saved = await session.get(MaxInboxRow, row.id)
        assert saved.status == "done" and saved.payload is None


@pytest.mark.asyncio
async def test_webhook_does_not_ack_failed_commit(monkeypatch):
    monkeypatch.setattr(settings, "max_bot_token", "test")
    monkeypatch.setattr(settings, "max_webhook_secret", "s" * 32)

    async def fail(_):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(app.state, "max_inbox", SimpleNamespace(accept=fail), raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.post(
            "/webhooks/max", json=event(), headers={"X-Max-Bot-Api-Secret": "s" * 32}
        )
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_poison_event_has_bounded_attempts(store):
    inbox = MaxInbox(store)
    await inbox.accept(event())
    for attempt in range(5):
        row = await inbox.claim()
        assert row.attempts == attempt + 1
        await inbox.finish(row.id, success=False)
        async with store.sessions.begin() as session:
            await session.execute(update(MaxInboxRow).values(available_at=row.created_at))
    assert await inbox.claim() is None
    async with store.sessions() as session:
        row = await session.scalar(select(MaxInboxRow))
        assert row.status == "failed" and row.payload is None


@pytest.mark.asyncio
async def test_max_transport_upload_token_retry_and_no_auth_on_cdn(tmp_path, monkeypatch):
    sent = 0

    async def no_wait(_):
        return None

    monkeypatch.setattr("voice_enhancer.infrastructure.max_api.asyncio.sleep", no_wait)

    def respond(request):
        nonlocal sent
        if request.url.host == "cdn.max.ru":
            assert "authorization" not in request.headers
            assert b'name="data"' in request.content
            return httpx.Response(200, json={})
        assert request.headers["Authorization"] == "secret"
        assert "access_token" not in request.url.params
        if request.url.path == "/uploads":
            assert request.url.params["type"] == "audio"
            return httpx.Response(
                200, json={"url": "https://cdn.max.ru/upload", "token": "audio-token"}
            )
        if request.url.path == "/messages":
            sent += 1
            assert (
                json.loads(request.content)["attachments"][0]["payload"]["token"] == "audio-token"
            )
            if sent == 1:
                return httpx.Response(400, json={"code": "attachment.not.ready"})
            return httpx.Response(200, json={"message": {"body": {"mid": "mid.result"}}})
        raise AssertionError(request.url.path)

    path = tmp_path / "result.m4a"
    path.write_bytes(b"media")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = MaxClient("secret", client=http)
        assert await client.send_media(7, path, "audio", "ready", {}) == "mid.result"
    assert sent == 2


@pytest.mark.asyncio
async def test_max_download_blocks_external_redirect_and_size(tmp_path):
    target = tmp_path / "voice.ogg"

    def respond(request):
        assert "authorization" not in request.headers
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})
        return httpx.Response(200, content=b"x" * 100)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = MaxClient("secret", client=http)
        with pytest.raises(ValueError, match="allowlist"):
            await client.download("https://cdn.max.ru/redirect", target, 1000)
        with pytest.raises(MediaValidationError, match="Размер"):
            await client.download("https://cdn.max.ru/file", target, 10)
        for url in ["http://cdn.max.ru/a", "https://max.ru.evil.com/a", "https://max.ru:8443/a"]:
            with pytest.raises(ValueError):
                client.validate_media_url(url)
    assert not target.exists()


@pytest.mark.asyncio
async def test_video_download_resolves_token_not_preview():
    def respond(request):
        assert request.url.path == "/videos/video-token"
        return httpx.Response(
            200,
            json={
                "urls": {
                    "mp4_1080": "https://cdn.max.ru/full",
                    "mp4_240": "https://cdn.max.ru/small",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = MaxClient("secret", client=http)
        assert (
            await client.attachment_url(
                {
                    "type": "video",
                    "payload": {"url": "https://cdn.max.ru/preview", "token": "video-token"},
                }
            )
            == "https://cdn.max.ru/full"
        )


class FakeMax:
    def __init__(self):
        self.messages = []
        self.answers = []
        self.downloads = 0

    async def attachment_url(self, attachment):
        return attachment["payload"]["url"]

    async def download(self, url, target, limit):
        self.downloads += 1
        target.write_bytes(b"source")

    async def send(self, chat_id, text, attachments=None):
        self.messages.append((chat_id, text, attachments))
        return "mid.preset"

    async def answer(self, callback_id, text=""):
        self.answers.append(text)

    async def edit(self, message_id, text):
        self.messages.append((message_id, text, None))

    async def send_media(self, chat_id, path, kind, text, keyboard):
        self.messages.append((chat_id, text, keyboard))
        return "mid.result"


@pytest.mark.asyncio
async def test_max_media_to_shared_worker_and_cross_user_callback(store, tmp_path, monkeypatch):
    from voice_enhancer.application.processing import ProcessedMedia

    class Queue:
        def __init__(self):
            self.enqueued = []

        async def enqueue(self, job_id):
            self.enqueued.append(job_id)

        async def acknowledge(self, job_id):
            pass

    class Processor:
        async def process(self, source, **kwargs):
            output = source.parent / "result.m4a"
            output.write_bytes(b"enhanced")
            return ProcessedMedia(output, "fake", 1.0)

    async def probe(_):
        return {"format": {"duration": "1"}, "streams": [{"codec_type": "audio"}]}

    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    client, queue = FakeMax(), Queue()
    bot = MaxBot(client, store, queue, SimpleNamespace(probe=probe), Path(settings.media_root))
    await bot.handle(event())
    await bot.handle(event())  # Resume same job; do not download twice.
    assert client.downloads == 1
    async with store.sessions() as session:
        from voice_enhancer.infrastructure.database import JobRow

        job = await session.scalar(select(JobRow))
    callback = {
        "update_type": "message_callback",
        "timestamp": 12346,
        "callback": {
            "callback_id": "cb1",
            "user": {"user_id": 10},
            "payload": f"p:{job.id}:natural",
        },
        "message": {"recipient": {"chat_id": 7}, "body": {"mid": "mid.status"}},
    }
    await bot.handle(callback)
    assert queue.enqueued == []
    callback["callback"]["user"]["user_id"] = 9
    await bot.handle(callback)
    await bot.handle(callback)
    assert queue.enqueued == [job.id]
    worker = MediaWorker(
        store=store, queue=queue, deliveries={"max": MaxDelivery(client)}, processor=Processor()
    )
    await worker.process_one(job.id)
    saved = await store.get(job.id)
    assert saved.status == JobStatus.COMPLETED.value
    assert saved.external_result_id == "mid.result"
    assert saved.result_message_id is None
    assert len(await store.events_for_user(9, channel="max")) == 2
    callback["callback"]["payload"] = f"r:{job.id}"
    await bot.handle(callback)
    async with store.sessions() as session:
        jobs = list(await session.scalars(select(JobRow)))
    assert len(jobs) == 2
    assert all(j.channel == "max" for j in jobs)
    deletion = event("mid.delete")
    deletion["message"]["body"] = {"mid": "mid.delete", "text": "/delete_my_data"}
    await bot.handle(deletion)
    assert await store.get(job.id) is None
    assert not Path(job.source_path).exists()


@pytest.mark.asyncio
async def test_delete_cancels_old_inbox_retries_but_keeps_new_uploads(store):
    inbox = MaxInbox(store)
    await inbox.accept(event("mid.old"))
    old = await inbox.claim()
    await inbox.finish(old.id, success=False)
    newer = event("mid.new")
    newer["timestamp"] = 12347
    await inbox.accept(newer)
    await store.request_user_deletion(9, channel="max", before_timestamp=12346)
    row = await inbox.claim()
    assert row.payload["message"]["body"]["mid"] == "mid.new"
    async with store.sessions() as session:
        cancelled = await session.get(MaxInboxRow, old.id)
        assert cancelled.payload is None and cancelled.status == "done"
